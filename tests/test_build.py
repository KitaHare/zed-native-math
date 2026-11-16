# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
specification = importlib.util.spec_from_file_location("build_helper", ROOT / "build.py")
builder = importlib.util.module_from_spec(specification)
specification.loader.exec_module(builder)


class BuildHelperTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        environment = mock.patch.dict(os.environ, {"CARGO_TARGET_DIR": "target", "CARGO_BUILD_TARGET": ""})
        environment.start()
        self.addCleanup(environment.stop)
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

    def test_verify_checks_revision_and_patch_compatibility(self):
        with mock.patch.object(builder, "BASE_COMMIT", self.revision):
            with mock.patch.object(builder, "PATCH", self.patch):
                with mock.patch.object(builder.sys, "argv", ["build.py", "verify", "--source", str(self.source)]):
                    self.assertEqual(builder.main(), 0)

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
        with mock.patch.object(builder.sys, "platform", "darwin"):
            with self.assertRaisesRegex(builder.BuildError, "must match"):
                builder.package_application(self.source, template, destination)
        self.assertFalse(destination.exists())

    def test_macos_package_contains_version_metadata_and_built_binary(self):
        template = self.base / "Official.app"
        (template / "Contents/MacOS").mkdir(parents=True)
        information = {"CFBundleShortVersionString": builder.MANIFEST["zed"]["tag"].removeprefix("v")}
        with (template / "Contents/Info.plist").open("wb") as stream:
            plistlib.dump(information, stream)
        with mock.patch.object(builder.sys, "platform", "darwin"):
            binary = builder.executable_path(self.source)
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b"community binary")
            destination = self.base / "Community.app"
            with mock.patch.object(builder, "run"):
                builder.package_application(self.source, template, destination)
        with (destination / "Contents/Info.plist").open("rb") as stream:
            packaged_information = plistlib.load(stream)
        self.assertEqual(set(packaged_information), {
            "CFBundleShortVersionString", "CFBundleName", "CFBundleDisplayName",
            "CFBundleIdentifier", "CFBundleExecutable", "ZedMathBaseCommit", "LSEnvironment",
        })
        self.assertEqual(packaged_information["ZedMathBaseCommit"], builder.BASE_COMMIT)
        self.assertEqual((destination / "Contents/MacOS/zed").read_bytes(), b"community binary")

    def test_apply_preserves_utf8_readme_and_writes_lf_notice(self):
        readme = self.source / "README.md"
        text = "数学公式 — upstream README\n"
        readme.write_text(text, encoding="utf-8")
        self.git("add", "README.md")
        self.git("commit", "-m", "unicode readme")
        revision = self.git("rev-parse", "HEAD").decode().strip()
        builder.apply_patch(self.source, self.patch, revision)
        self.assertEqual(readme.read_text(encoding="utf-8"), builder.NOTICE + "\n" + text)
        self.assertNotIn(b"\r\n", readme.read_bytes())

    def test_windows_build_includes_cli_without_metal_features(self):
        with mock.patch.object(builder.sys, "platform", "win32"):
            command = builder.cargo_command("build")
        self.assertIn("cli", command)
        self.assertIn("--locked", command)
        self.assertEqual(command[command.index("--features") + 1], "util/debug-embed")
        self.assertNotIn("--release", command)

    def test_macos_build_keeps_existing_features_and_binary(self):
        with mock.patch.object(builder.sys, "platform", "darwin"):
            command = builder.cargo_command("build")
        self.assertEqual(command, ["cargo", "build", "--locked", "-p", "zed", "--bin", "zed",
                                   "--features", builder.MACOS_FEATURES])

    def test_native_test_runs_entire_markdown_library(self):
        with mock.patch.object(builder.sys, "platform", "win32"):
            command = builder.cargo_command("test")
        self.assertIn("--lib", command)
        self.assertIn("markdown", command)
        self.assertEqual(command[command.index("--") + 1:], ["--test-threads=1"])

    def test_windows_update_disable_is_embedded_and_job_override_is_preserved(self):
        with mock.patch.object(builder.sys, "platform", "win32"):
            with mock.patch.dict(os.environ, {"CARGO_BUILD_JOBS": "4"}):
                environment = builder.build_environment()
        self.assertEqual(environment["ZED_UPDATE_EXPLANATION"], builder.UPDATE_EXPLANATION)
        self.assertEqual(environment["CARGO_BUILD_JOBS"], "4")
        self.assertEqual(environment["RUSTUP_TOOLCHAIN"], builder.MANIFEST["rust_toolchain"])
        self.assertEqual(environment["RELEASE_CHANNEL"], "stable")
        self.assertEqual(environment["RELEASE_VERSION"], builder.MANIFEST["zed"]["tag"].removeprefix("v"))
        self.assertEqual(environment["ZED_COMMIT_SHA"], builder.BASE_COMMIT)

    def test_explicit_manifest_keeps_default_pinned_contract_and_resolves_relative_patch(self):
        manifest = self.base / "candidate.json"
        manifest.write_text(json.dumps({
            "zed": {"tag": "v9.8.7", "commit": self.revision},
            "patch": {"file": self.patch.name}, "rust_toolchain": "1.98.1",
        }), encoding="utf-8")
        with mock.patch.multiple(builder, MANIFEST=builder.MANIFEST, MANIFEST_PATH=builder.MANIFEST_PATH,
                                 PATCH=builder.PATCH, BASE_COMMIT=builder.BASE_COMMIT):
            with mock.patch.object(builder.sys, "argv", ["build.py", "verify", "--source", str(self.source),
                                                        "--manifest", str(manifest)]):
                self.assertEqual(builder.main(), 0)
            self.windows_package_inputs()
            destination = self.base / "Candidate"
            with mock.patch.object(builder.sys, "platform", "win32"):
                builder.package_windows_application(self.source, destination)
            self.assertEqual((destination / "native-math-manifest.json").read_bytes(), manifest.read_bytes())
            self.assertEqual(builder.PATCH, self.patch)
            self.assertEqual(builder.BASE_COMMIT, self.revision)
            self.assertEqual(builder.build_environment()["RELEASE_VERSION"], "9.8.7")

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell entry point")
    def test_windows_entry_point_forwards_explicit_manifest_once(self):
        script = self.base / "build-windows.ps1"
        shutil.copy2(ROOT / script.name, script)
        (self.base / "build.py").write_text("import json,sys\nprint(json.dumps(sys.argv[1:]))\n", encoding="utf-8")
        manifest = self.base / "candidate manifest.json"
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", script, "verify", "-Source", self.source, "-Manifest", manifest],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(json.loads(result.stdout), ["verify", "--source", str(self.source), "--manifest", str(manifest)])

    def test_executable_paths_follow_platform_profile_and_target_environment(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch.object(builder.sys, "platform", "win32"):
                self.assertEqual(builder.executable_path(self.source), self.source / "target/debug/zed.exe")
                self.assertEqual(builder.executable_path(self.source, True), self.source / "target/release/zed.exe")
            with mock.patch.object(builder.sys, "platform", "darwin"):
                self.assertEqual(builder.executable_path(self.source), self.source / "target/debug/zed")
        with mock.patch.dict(os.environ, {"CARGO_TARGET_DIR": "custom-target",
                                         "CARGO_BUILD_TARGET": "x86_64-pc-windows-msvc"}):
            with mock.patch.object(builder.sys, "platform", "win32"):
                self.assertEqual(builder.executable_path(self.source),
                                 self.source / "custom-target/x86_64-pc-windows-msvc/debug/zed.exe")

    def windows_package_inputs(self, release=False):
        with mock.patch.object(builder.sys, "platform", "win32"):
            binary = builder.executable_path(self.source, release)
        binary.parent.mkdir(parents=True)
        for name in ("zed.exe", "cli.exe", "conpty.dll", "OpenConsole.exe"):
            (binary.parent / name).write_bytes(name.encode())
        for name in ("LICENSE-GPL", "LICENSE-APACHE"):
            (self.source / name).write_text(name)
        return binary

    def test_windows_package_contains_gui_cli_terminal_and_licenses(self):
        self.windows_package_inputs()
        destination = self.base / "Community Math"
        with mock.patch.object(builder.sys, "platform", "win32"):
            builder.package_windows_application(self.source, destination)
        self.assertEqual((destination / "Zed.exe").read_bytes(), b"zed.exe")
        self.assertEqual((destination / "bin/zed.exe").read_bytes(), b"cli.exe")
        self.assertEqual((destination / "conpty.dll").read_bytes(), b"conpty.dll")
        self.assertEqual((destination / "OpenConsole.exe").read_bytes(), b"OpenConsole.exe")
        self.assertTrue((destination / "LICENSE").is_file())
        self.assertTrue((destination / "licenses/ZED-LICENSE-GPL").is_file())
        self.assertEqual((destination / "native-math-manifest.json").read_bytes(),
                         (ROOT / "manifest.json").read_bytes())

    def test_windows_missing_terminal_runtime_does_not_create_package(self):
        binary = self.windows_package_inputs()
        (binary.parent / "conpty.dll").unlink()
        destination = self.base / "Community Math"
        with mock.patch.object(builder.sys, "platform", "win32"):
            with self.assertRaisesRegex(builder.BuildError, "conpty.dll"):
                builder.package_windows_application(self.source, destination)
        self.assertFalse(destination.exists())

    def test_windows_existing_package_is_preserved(self):
        destination = self.base / "Existing"
        destination.mkdir()
        (destination / "Zed.exe").write_bytes(b"existing app")
        with mock.patch.object(builder.sys, "platform", "win32"):
            with self.assertRaisesRegex(builder.BuildError, "refusing to overwrite"):
                builder.package_windows_application(self.source, destination)
        self.assertEqual((destination / "Zed.exe").read_bytes(), b"existing app")

    def test_release_build_and_package_use_same_profile(self):
        self.windows_package_inputs(release=True)
        destination = self.base / "Release"
        with mock.patch.object(builder.sys, "platform", "win32"):
            self.assertIn("--release", builder.cargo_command("build", release=True))
            builder.package_windows_application(self.source, destination, release=True)
        self.assertTrue((destination / "Zed.exe").is_file())

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell entry point")
    def test_full_build_prerequisites_leave_markdown_tests_available(self):
        script = self.base / "build-windows.ps1"
        shutil.copy2(ROOT / script.name, script)
        tools = self.base / "MSVC/bin/HostX64/x64"
        tools.mkdir(parents=True)
        (tools / "cl.exe").write_bytes(b"compiler path fixture")
        (tools / "cargo.exe").write_bytes(b"cargo path fixture")
        (self.base / "MSVC/lib/spectre/x64").mkdir(parents=True)
        sdk = self.base / "SDK"
        header = sdk / "Include/10.0.22621.0/shared/sdkddkver.h"
        header.parent.mkdir(parents=True)
        header.write_text("#define NTDDI_WIN10_NI 0x0A00000C\n", encoding="ascii")
        (self.base / "build.py").write_text("print('native test reached')\n", encoding="utf-8")
        environment = os.environ.copy()
        environment.update(VSCMD_ARG_TGT_ARCH="x64", WindowsSdkDir=str(sdk),
                           WindowsSDKVersion="10.0.22621.0\\", VCToolsVersion="14.43.34808")
        path_key = next(key for key in environment if key.lower() == "path")
        environment[path_key] = str(tools) + os.pathsep + environment[path_key]
        for action, expected in [("build", 1), ("test", 0)]:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", script, action, "-Source", self.source],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, expected, result.stderr.decode(errors="replace"))
            if action == "build":
                self.assertIn(b"NTDDI_WIN11_GE", result.stderr)
                self.assertNotIn(b"native test reached", result.stdout)
            else:
                self.assertIn(b"native test reached", result.stdout)
        header.write_text("#define NTDDI_WIN11_GE 0x0A000010\n", encoding="ascii")
        for version, action, expected in [("14.43.34808", "build", 1),
                                          ("14.43.34808", "test", 0),
                                          ("14.44.35207", "build", 0)]:
            environment["VCToolsVersion"] = version
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-File", script, action, "-Source", self.source],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            self.assertEqual(result.returncode, expected, result.stderr.decode(errors="replace"))
            if expected:
                self.assertIn(b"MSVC 14.44", result.stderr)
            else:
                self.assertIn(b"native test reached", result.stdout)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell entry point")
    def test_windows_entry_point_prefers_project_local_nasm(self):
        script = self.base / "build-windows.ps1"
        shutil.copy2(ROOT / script.name, script)
        nasm = self.base / ".work/tools/nasm-3.02-win64/nasm-3.02/nasm.exe"
        nasm.parent.mkdir(parents=True)
        nasm.write_bytes(b"path lookup fixture")
        (self.base / "build.py").write_text(
            "import shutil\nprint(shutil.which('nasm'))\n", encoding="utf-8",
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", script, "verify", "-Source", self.source],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertEqual(Path(result.stdout.decode().strip()), nasm)

    @unittest.skipUnless(os.name == "nt", "Windows PowerShell entry point")
    def test_windows_entry_point_allows_stderr_and_preserves_exit_code(self):
        script = self.base / "build-windows.ps1"
        shutil.copy2(ROOT / script.name, script)
        (self.base / "build.py").write_text(
            "import sys\nprint('Cargo progress on stderr', file=sys.stderr)\nsys.exit(23)\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-File", script, "verify", "-Source", self.source],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.assertEqual(result.returncode, 23, result.stderr.decode(errors="replace"))
        self.assertIn(b"Cargo progress on stderr", result.stderr)


if __name__ == "__main__":
    unittest.main()
