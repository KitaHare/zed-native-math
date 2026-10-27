# SPDX-License-Identifier: GPL-3.0-or-later
import hashlib
import importlib.util
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("build_helper", ROOT / "build.py")
builder = importlib.util.module_from_spec(specification)
specification.loader.exec_module(builder)


class BuildHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.source.mkdir()
        self.git("init")
        self.git("config", "user.name", "Build helper test")
        self.git("config", "user.email", "build-test@example.invalid")
        (self.source / "README.md").write_text("Upstream README\n")
        (self.source / "renderer.rs").write_text("original renderer\n")
        self.git("add", ".")
        self.git("commit", "-m", "base")
        self.revision = self.git("rev-parse", "HEAD").decode().strip()
        (self.source / "renderer.rs").write_text("native math renderer\n")
        self.patch = self.base / "math.patch"
        self.patch.write_bytes(self.git("diff", "--binary", "--full-index"))
        self.git("restore", "renderer.rs")

    def git(self, *arguments):
        return subprocess.check_output(["git", *arguments], cwd=self.source, stderr=subprocess.STDOUT)

    def test_apply_is_version_checked_preserves_notice_and_is_idempotent(self):
        builder.apply_patch(self.source, self.patch, self.revision)
        self.assertEqual((self.source / "renderer.rs").read_text(), "native math renderer\n")
        self.assertTrue((self.source / "README.md").read_text().startswith(builder.NOTICE))
        first_status = self.git("status", "--porcelain")
        builder.apply_patch(self.source, self.patch, self.revision)
        self.assertEqual(self.git("status", "--porcelain"), first_status)

    def test_wrong_revision_does_not_modify_checkout(self):
        with self.assertRaises(builder.BuildError):
            builder.apply_patch(self.source, self.patch, "0" * 40)
        self.assertEqual(self.git("status", "--porcelain"), b"")
        self.assertEqual((self.source / "renderer.rs").read_text(), "original renderer\n")

    def test_local_changes_are_preserved_and_not_overwritten(self):
        (self.source / "renderer.rs").write_text("user changes\n")
        with self.assertRaises(builder.BuildError):
            builder.apply_patch(self.source, self.patch, self.revision)
        self.assertEqual((self.source / "renderer.rs").read_text(), "user changes\n")
        self.assertEqual((self.source / "README.md").read_text(), "Upstream README\n")

    def test_checksum_mismatch_stops_release_use(self):
        expected = hashlib.sha256(self.patch.read_bytes()).hexdigest()
        builder.verify_checksum(self.patch, expected)
        self.patch.write_bytes(self.patch.read_bytes() + b"altered")
        with self.assertRaises(builder.BuildError):
            builder.verify_checksum(self.patch, expected)

    def test_existing_application_is_never_overwritten(self):
        destination = self.base / "Existing.app"
        destination.mkdir()
        marker = destination / "user-file"
        marker.write_text("keep")
        with self.assertRaises(builder.BuildError):
            builder.package_application(self.source, self.base / "missing-template.app", destination)
        self.assertEqual(marker.read_text(), "keep")

    def test_mismatched_template_version_does_not_create_destination(self):
        template = self.base / "Official.app"
        (template / "Contents").mkdir(parents=True)
        with (template / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump({"CFBundleShortVersionString": "0.0.0"}, stream)
        destination = self.base / "Community.app"
        with self.assertRaises(builder.BuildError):
            builder.package_application(self.source, template, destination)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
