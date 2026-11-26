# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import io
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_update", ROOT / "windows_update.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


def make_release_zip(tag, commit, *, wrapped=False, extra_files=None):
    buffer = io.BytesIO()
    prefix = f"Zed Native Math ({tag})/" if wrapped else ""
    manifest = {"zed": {"tag": update.base_zed_tag(tag), "commit": commit}}
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(prefix + "Zed.exe", b"gui fixture")
        bundle.writestr(prefix + "bin/zed.exe", b"cli fixture")
        bundle.writestr(prefix + "conpty.dll", b"conpty fixture")
        bundle.writestr(prefix + "OpenConsole.exe", b"console fixture")
        bundle.writestr(prefix + "native-math-manifest.json", json.dumps(manifest))
        for name, content in (extra_files or {}).items():
            bundle.writestr(name, content)
    buffer.seek(0)
    return buffer


@unittest.skipUnless(os.name == "nt", "Windows update/launcher integration")
class WindowsUpdateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {"LOCALAPPDATA": str(self.root / "profile")})
        environment.start()
        self.addCleanup(environment.stop)
        self.updater = update.Updater(self.root)
        self.current = self.release("v1.0.0", "a" * 40)
        self.candidate = self.release("v1.1.0", "b" * 40)
        update.atomic_json(self.updater.state_path, {
            "enabled": True,
            "mode": "release",
            "repository": "KitaHare/zed-native-math",
            "check_interval_seconds": update.INTERVAL_SECONDS,
            "current": self.current,
            "previous": None,
            "pending": None,
            "phase": "installed",
            "last_checked": None,
        })

    def release(self, tag, commit):
        directory = self.root / "dist/windows-versions" / tag
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "bin").mkdir(exist_ok=True)
        (directory / "Zed.exe").write_bytes(b"application fixture")
        (directory / "bin/zed.exe").write_bytes(b"cli fixture")
        (directory / "conpty.dll").write_bytes(b"conpty fixture")
        (directory / "OpenConsole.exe").write_bytes(b"console fixture")
        manifest_path = directory / "native-math-manifest.json"
        update.atomic_json(manifest_path, {"zed": {"tag": update.base_zed_tag(tag), "commit": commit}})
        return {
            "tag": tag,
            "zed_tag": update.base_zed_tag(tag),
            "commit": commit,
            "directory": str(directory),
            "manifest": str(manifest_path),
        }

    def release_info(self, tag):
        return {
            "tag": tag,
            "asset_name": f"zed-native-math-{tag}-windows-x64.zip",
            "download_url": f"https://github.com/KitaHare/zed-native-math/releases/download/{tag}/zed-native-math-{tag}-windows-x64.zip",
        }

    def test_initialize_migrates_legacy_source_state_and_preserves_pointers(self):
        previous = self.release("v0.9.0", "c" * 40)
        update.atomic_json(self.updater.state_path, {
            "enabled": False,
            "current": self.current,
            "previous": previous,
            "pending": self.candidate,
            "phase": "failed",
            "failed_tag": "v1.2.0",
            "failure_detail": "git apply conflict",
            "last_error": "git apply conflict",
            "last_error_kind": "update",
            "notified_failure": "v1.2.0",
        })
        self.updater.initialize()
        state = self.updater.state()
        self.assertTrue(state["enabled"])
        self.assertEqual(state["mode"], "release")
        self.assertEqual(state["current"], self.current)
        self.assertEqual(state["previous"], previous)
        self.assertEqual(state["pending"], self.candidate)
        self.assertEqual(state["phase"], "ready")
        self.assertIsNone(state["failed_tag"])
        self.assertIsNone(state["last_error"])

    def test_initialize_uses_existing_local_package_without_network(self):
        fresh_root = self.root / "local-first"
        updater = update.Updater(fresh_root)
        package = fresh_root / "dist/Zed Native Math (Community)"
        package.mkdir(parents=True)
        (package / "Zed.exe").write_bytes(b"local app")
        update.atomic_json(package / "native-math-manifest.json", {
            "zed": {"tag": "v1.29.2", "commit": "a" * 40},
        })
        with mock.patch.object(update, "latest_release") as fetch:
            updater.initialize()
            fetch.assert_not_called()
        state = updater.state()
        self.assertEqual(state["mode"], "release")
        self.assertEqual(state["current"]["tag"], "v1.29.2")
        self.assertEqual(Path(state["current"]["directory"]), package)

    def test_initialize_bootstraps_directly_from_github_release_when_no_local_build(self):
        fresh_root = self.root / "cloud-first"
        updater = update.Updater(fresh_root)
        downloaded = self.release("v1.30.2", "d" * 40)
        with mock.patch.object(update, "latest_release", return_value=self.release_info("v1.30.2")), \
             mock.patch.object(updater, "download_candidate", return_value=downloaded) as download:
            updater.initialize()
        download.assert_called_once()
        state = updater.state()
        self.assertEqual(state["mode"], "release")
        self.assertEqual(state["current"], downloaded)

    def test_release_tag_validation_and_numeric_order(self):
        self.assertGreater(update.version("v1.10.0"), update.version("v1.9.99"))
        self.assertGreater(update.version("v1.30.2-math.2"), update.version("v1.30.2"))
        self.assertGreater(update.version("v1.30.3"), update.version("v1.30.2-math.9"))
        self.assertEqual(update.base_zed_tag("v1.30.2-math.2"), "v1.30.2")
        for tag in ("main", "v1.1.0-pre", "v1.1.0-rc.1", "../v1.1.0", None):
            with self.assertRaises(update.UpdateError):
                update.version(tag)

    def test_latest_release_selects_windows_zip_and_rejects_draft_prerelease_or_missing_asset(self):
        def fake_response(payload):
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
            return response

        valid = {
            "tag_name": "v1.30.2",
            "draft": False,
            "prerelease": False,
            "assets": [
                {"name": "zed-v1.30.2-native-math.patch", "browser_download_url": "https://github.com/x/y/patch"},
                {"name": "zed-native-math-v1.30.2-windows-x64.zip", "browser_download_url": "https://github.com/x/y/app.zip"},
            ],
        }
        with mock.patch.object(update.urllib.request, "urlopen", return_value=fake_response(valid)):
            info = update.latest_release("KitaHare/zed-native-math")
        self.assertEqual(info["tag"], "v1.30.2")
        self.assertEqual(info["download_url"], "https://github.com/x/y/app.zip")

        for bad_payload, pattern in [
            ({**valid, "prerelease": True}, "stable"),
            ({**valid, "draft": True}, "stable"),
            ({**valid, "assets": []}, "Windows x64 package"),
            ({**valid, "assets": [{"name": "zed-native-math-v1.30.2-windows-x64.zip", "browser_download_url": "http://insecure.example/app.zip"}]}, "HTTPS"),
        ]:
            with self.subTest(pattern=pattern), \
                 mock.patch.object(update.urllib.request, "urlopen", return_value=fake_response(bad_payload)):
                with self.assertRaisesRegex(update.UpdateError, pattern):
                    update.latest_release("KitaHare/zed-native-math")

    def test_download_candidate_validates_flat_and_wrapped_archives_and_deletes_temp_zip(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                commit = "e" * 40
                payload = make_release_zip("v1.30.2-math.2", commit, wrapped=wrapped)
                response = mock.MagicMock()
                response.__enter__.return_value = payload
                with mock.patch.object(update.urllib.request, "urlopen", return_value=response), \
                     mock.patch.object(self.updater, "validate_package") as validate:
                    result = self.updater.download_candidate(self.release_info("v1.30.2-math.2"))
                validate.assert_called_once()
                self.assertEqual(result["tag"], "v1.30.2-math.2")
                self.assertEqual(result["zed_tag"], "v1.30.2")
                self.assertEqual(result["commit"], commit)
                self.assertTrue((Path(result["directory"]) / "Zed.exe").is_file())
                self.assertEqual(list((self.updater.work / "downloads").glob("*.zip")), [])

    def test_download_candidate_blocks_zip_slip_and_cleans_up_failed_extraction(self):
        evil = make_release_zip("v1.30.2", "f" * 40, extra_files={"../escaped.txt": b"evil"})
        response = mock.MagicMock()
        response.__enter__.return_value = evil
        before_versions = set((self.root / "dist/windows-versions").iterdir())
        with mock.patch.object(update.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(update.UpdateError, "invalid extraction path"):
                self.updater.download_candidate(self.release_info("v1.30.2"))
        self.assertEqual(set((self.root / "dist/windows-versions").iterdir()), before_versions)
        self.assertFalse((self.root / "dist/escaped.txt").exists())
        self.assertEqual(list((self.updater.work / "downloads").glob("*.zip")), [])

    def test_download_candidate_rejects_mismatched_manifest_tag_and_cleans_up(self):
        bad_zip = make_release_zip("v1.29.0", "f" * 40)
        response = mock.MagicMock()
        response.__enter__.return_value = bad_zip
        before_versions = set((self.root / "dist/windows-versions").iterdir())
        with mock.patch.object(update.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(update.UpdateError, "manifest mismatch"):
                self.updater.download_candidate(self.release_info("v1.30.2"))
        self.assertEqual(set((self.root / "dist/windows-versions").iterdir()), before_versions)

    def test_concurrent_worker_does_not_download(self):
        with update.lock(self.updater.work / "update.lock"):
            with mock.patch.object(self.updater, "download_candidate") as download:
                self.assertEqual(self.updater.sync(True), "already-running")
                download.assert_not_called()

    def test_current_version_is_not_redownloaded(self):
        with mock.patch.object(update, "latest_release", return_value=self.release_info("v1.0.0")):
            with mock.patch.object(self.updater, "download_candidate") as download:
                self.assertEqual(self.updater.sync(True), "up-to-date")
                download.assert_not_called()
        self.assertEqual(self.updater.state()["current"], self.current)

    def test_success_stages_without_replacing_current(self):
        with mock.patch.object(update, "latest_release", return_value=self.release_info("v1.1.0")):
            with mock.patch.object(self.updater, "download_candidate", return_value=self.candidate):
                self.assertEqual(self.updater.sync(True), "ready")
        state = self.updater.state()
        self.assertEqual(state["current"], self.current)
        self.assertEqual(state["pending"], self.candidate)
        self.assertEqual(state["phase"], "ready")

    def test_failure_keeps_current_and_pending_and_notifies_once(self):
        self.updater.change(pending=self.candidate)
        with mock.patch.object(update, "latest_release", return_value=self.release_info("v1.2.0")):
            with mock.patch.object(self.updater, "download_candidate", side_effect=update.UpdateError("corrupt zip")):
                self.assertEqual(self.updater.sync(True), "failed")
        state = self.updater.state()
        self.assertEqual(state["current"], self.current)
        self.assertEqual(state["pending"], self.candidate)
        self.assertIn("corrupt zip", state["last_error"])
        with mock.patch.object(update, "stable_is_running", return_value=True), \
             mock.patch.object(update.subprocess, "Popen"), \
             mock.patch("ctypes.windll.user32.MessageBoxW") as alert:
            self.updater.launch([])
            self.updater.launch([])
        alert.assert_called_once()
        self.assertIn("corrupt zip", alert.call_args.args[1])

    def test_unchanged_failure_is_not_retried_until_explicit_force(self):
        self.updater.change(failed_tag="v1.1.0", failure_detail="smoke check failed")
        with mock.patch.object(update, "latest_release", side_effect=TimeoutError("offline")):
            self.assertEqual(self.updater.sync(True), "failed")
        self.updater.change(last_checked="2000-01-01T00:00:00+00:00")
        with mock.patch.object(update, "latest_release", return_value=self.release_info("v1.1.0")):
            with mock.patch.object(self.updater, "download_candidate", return_value=self.candidate) as download:
                self.assertEqual(self.updater.sync(), "failed-awaiting-retry")
                download.assert_not_called()
                self.assertEqual(self.updater.state()["last_error"], "smoke check failed")
                self.assertEqual(self.updater.sync(True), "ready")
                download.assert_called_once()

    def test_disabled_and_recent_checks_do_not_contact_network(self):
        with mock.patch.object(update, "latest_release") as latest:
            self.updater.change(enabled=False)
            self.assertEqual(self.updater.sync(True), "disabled")
            self.updater.change(enabled=True, last_checked=update.now())
            self.assertEqual(self.updater.sync(), "not-due")
            latest.assert_not_called()

    def test_tls_disconnect_is_logged_without_popup_and_next_check_recovers(self):
        failure = update.urllib.error.URLError(
            ssl.SSLEOFError(8, "[SSL: UNEXPECTED_EOF_WHILE_READING] EOF occurred in violation of protocol"))
        with mock.patch.object(update.urllib.request, "urlopen", side_effect=failure) as request, \
             mock.patch.object(update.time, "sleep"):
            self.assertEqual(self.updater.sync(True), "failed")
        self.assertEqual(request.call_count, 3)
        state = self.updater.state()
        self.assertEqual(state["current"], self.current)
        self.assertIsNone(state.get("failed_tag"))
        with mock.patch.object(update, "stable_is_running", return_value=True), \
             mock.patch.object(update.subprocess, "Popen"), \
             mock.patch("ctypes.windll.user32.MessageBoxW") as alert:
            self.updater.launch([])
        alert.assert_not_called()
        log = self.updater.log.read_text(encoding="utf-8")
        self.assertIn("UNEXPECTED_EOF_WHILE_READING", log)

        self.updater.change(last_checked="2000-01-01T00:00:00+00:00")
        with mock.patch.object(update, "latest_release", return_value=self.release_info(self.current["tag"])), \
             mock.patch.object(self.updater, "download_candidate") as download:
            self.assertEqual(self.updater.sync(), "up-to-date")
        download.assert_not_called()
        self.assertIsNone(self.updater.state()["last_error"])

    def test_only_exit_allows_activation_and_prunes_stale_versions_and_db_backups(self):
        stale = self.release("v0.8.0", "1" * 40)
        database = self.root / "profile/Zed/db/0-stable"
        database.mkdir(parents=True)
        (database / "db.sqlite").write_bytes(b"pre-migration database fixture")
        backups_root = self.updater.work / "database-backups"
        for stamp in ("1000", "2000", "3000", "4000"):
            (backups_root / stamp).mkdir(parents=True)
            (backups_root / stamp / "db.sqlite").write_bytes(b"old backup")

        self.updater.change(pending=self.candidate)
        self.assertEqual(self.updater.promote(True)["current"], self.current)
        state = self.updater.promote(False)
        self.assertEqual(state["current"], self.candidate)
        self.assertEqual(state["previous"], self.current)
        self.assertIsNone(state["pending"])
        self.assertFalse(Path(stale["directory"]).exists())
        self.assertTrue(Path(self.current["directory"]).exists())
        self.assertTrue(Path(self.candidate["directory"]).exists())
        remaining_backups = sorted(p.name for p in backups_root.iterdir() if p.is_dir())
        self.assertEqual(len(remaining_backups), update.MAX_DATABASE_BACKUPS)
        snapshot = Path(state["last_database_backup"]) / "0-stable/db.sqlite"
        self.assertEqual(snapshot.read_bytes(), (database / "db.sqlite").read_bytes())

    def test_cleanup_legacy_build_caches_preserves_active_releases_and_supports_dry_run(self):
        previous = self.release("v0.9.0", "c" * 40)
        stale = self.release("v0.7.0", "2" * 40)
        self.updater.change(previous=previous, pending=self.candidate)
        for rel in (
            ".work/zed-upstream/target/big.rlib",
            ".work/update-sources/v1.30.2/file.rs",
            ".work/cargo/bin/cargo.exe",
            ".work/rustup/toolchains/1.98.1/rustc.exe",
            ".work/windows-updates/webrtc/webrtc.lib",
            ".work/windows-updates/acp-1.29.2-abc/data.bin",
            ".work/desktop-shortcut-backup/Zed-original.lnk",
            "dist/Zed Native Math (Community)/Zed.exe",
        ):
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"x" * 64)

        preview = self.updater.cleanup_legacy_build_caches(dry_run=True)
        self.assertTrue(preview["dry_run"])
        self.assertGreater(preview["freed_bytes"], 0)
        self.assertTrue((self.root / ".work/zed-upstream").exists())

        actual = self.updater.cleanup_legacy_build_caches(dry_run=False)
        self.assertFalse(actual["dry_run"])
        self.assertFalse((self.root / ".work/zed-upstream").exists())
        self.assertFalse((self.root / ".work/cargo").exists())
        self.assertFalse((self.root / ".work/rustup").exists())
        self.assertFalse((self.root / ".work/windows-updates/webrtc").exists())
        self.assertFalse((self.root / ".work/windows-updates/acp-1.29.2-abc").exists())
        self.assertFalse(Path(stale["directory"]).exists())
        self.assertFalse((self.root / "dist/Zed Native Math (Community)").exists())
        # Active, previous, pending, state, and shortcut backups must survive.
        self.assertTrue(Path(self.current["directory"]).exists())
        self.assertTrue(Path(previous["directory"]).exists())
        self.assertTrue(Path(self.candidate["directory"]).exists())
        self.assertTrue(self.updater.state_path.is_file())
        self.assertTrue((self.root / ".work/desktop-shortcut-backup/Zed-original.lnk").is_file())

    def test_rollback_waits_for_exit_and_holds_rejected_release(self):
        self.updater.change(current=self.candidate, previous=self.current)
        with self.assertRaisesRegex(update.UpdateError, "exit"):
            self.updater.rollback(True)
        self.updater.rollback(False)
        state = self.updater.state()
        self.assertEqual(state["current"], self.current)
        self.assertEqual(state["held_tag"], "v1.1.0")
        with mock.patch.object(update, "latest_release", return_value=self.release_info("v1.1.0")):
            with mock.patch.object(self.updater, "download_candidate") as download:
                self.assertEqual(self.updater.sync(True), "held-after-rollback")
                download.assert_not_called()

    def test_launcher_passes_file_arguments_and_removes_stateless_test_mode(self):
        self.updater.change(enabled=False)
        with mock.patch.object(update, "stable_is_running", return_value=True):
            with mock.patch.dict(os.environ, {"ZED_STATELESS": "true"}):
                with mock.patch.object(update.subprocess, "Popen") as launch:
                    self.updater.launch(["C:/中文文档/note.md:3"])
        self.assertEqual(launch.call_args.args[0],
                         [str(Path(self.current["directory"]) / "Zed.exe"), "C:/中文文档/note.md:3"])
        self.assertNotIn("ZED_STATELESS", launch.call_args.kwargs["env"])

    def test_custom_data_instances_do_not_block_switch_but_unknown_process_does(self):
        for processes, expected in [([], False),
                                    ([{"CommandLine": 'Zed.exe --user-data-dir "test"'}], False),
                                    ([{"CommandLine": 'Zed.exe --crash-handler "handler"'}], False),
                                    ([{"CommandLine": 'Zed.exe'}], True),
                                    ([{"CommandLine": None}], True)]:
            with mock.patch.object(update, "run", return_value=json.dumps(processes)):
                self.assertEqual(update.stable_is_running(), expected)

    def test_package_smoke_checks_required_files_gui_and_cli_identity(self):
        recipe = {"zed": {"tag": "v1.1.0", "commit": "b" * 40}}
        directory = Path(self.candidate["directory"])
        with mock.patch.object(self.updater, "command", side_effect=[
            "Usage: Zed [PATHS_OR_URLS]...",
            "Zed 1.1.0 " + "b" * 40 + " - " + str(directory / "Zed.exe"),
        ]):
            self.updater.validate_package(directory, recipe)
        with mock.patch.object(self.updater, "command", side_effect=["PATHS_OR_URLS", "Zed 1.0.0 wrong.exe"]):
            with self.assertRaises(update.UpdateError):
                self.updater.validate_package(directory, recipe)
        (directory / "conpty.dll").unlink()
        with self.assertRaisesRegex(update.UpdateError, "conpty.dll"):
            self.updater.validate_package(directory, recipe)


if __name__ == "__main__":
    unittest.main()
