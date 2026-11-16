# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("windows_update", ROOT / "windows_update.py")
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


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
            "enabled": True, "current": self.current, "previous": None, "pending": None,
            "phase": "installed", "last_checked": None,
        })

    def release(self, tag, commit):
        directory = self.root / "dist" / tag
        directory.mkdir(parents=True)
        (directory / "Zed.exe").write_bytes(b"application fixture")
        return {"tag": tag, "commit": commit, "directory": str(directory),
                "manifest": str(self.root / (tag + ".json"))}

    def test_stable_tag_validation_and_numeric_order(self):
        self.assertGreater(update.version("v1.10.0"), update.version("v1.9.99"))
        for tag in ("main", "v1.1.0-pre", "../v1.1.0", None):
            with self.assertRaises(update.UpdateError):
                update.version(tag)

    def test_rejects_preview_release_from_endpoint(self):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "tag_name": "v1.1.0", "prerelease": True, "draft": False,
        }).encode()
        with mock.patch.object(update.urllib.request, "urlopen", return_value=response):
            with self.assertRaisesRegex(update.UpdateError, "stable"):
                update.latest_release()

    def test_concurrent_worker_does_not_build(self):
        with update.lock(self.updater.work / "update.lock"):
            with mock.patch.object(self.updater, "build_candidate") as build:
                self.assertEqual(self.updater.sync(True), "already-running")
                build.assert_not_called()

    def test_current_version_is_not_rebuilt(self):
        with mock.patch.object(update, "latest_release", return_value="v1.0.0"):
            with mock.patch.object(self.updater, "build_candidate") as build:
                self.assertEqual(self.updater.sync(True), "up-to-date")
                build.assert_not_called()
        self.assertEqual(self.updater.state()["current"], self.current)

    def test_success_stages_without_replacing_current(self):
        with mock.patch.object(update, "latest_release", return_value="v1.1.0"):
            with mock.patch.object(self.updater, "build_candidate", return_value=self.candidate):
                self.assertEqual(self.updater.sync(True), "ready")
        state = self.updater.state()
        self.assertEqual(state["current"], self.current)
        self.assertEqual(state["pending"], self.candidate)
        self.assertEqual(state["phase"], "ready")

    def test_failure_keeps_current_and_pending_and_records_reason(self):
        self.updater.change(pending=self.candidate)
        with mock.patch.object(update, "latest_release", return_value="v1.2.0"):
            with mock.patch.object(self.updater, "build_candidate", side_effect=update.UpdateError("patch conflict")):
                self.assertEqual(self.updater.sync(True), "failed")
        state = self.updater.state()
        self.assertEqual(state["current"], self.current)
        self.assertEqual(state["pending"], self.candidate)
        self.assertIn("patch conflict", state["last_error"])

    def test_unchanged_failure_is_not_rebuilt_until_explicit_retry(self):
        self.updater.change(failed_tag="v1.1.0", failure_detail="compiler error")
        with mock.patch.object(update, "latest_release", return_value="v1.1.0"):
            with mock.patch.object(self.updater, "build_candidate", return_value=self.candidate) as build:
                self.assertEqual(self.updater.sync(), "failed-awaiting-retry")
                build.assert_not_called()
                self.assertEqual(self.updater.sync(True), "ready")
                build.assert_called_once()

    def test_disabled_and_recent_checks_do_not_contact_network(self):
        with mock.patch.object(update, "latest_release") as latest:
            self.updater.change(enabled=False)
            self.assertEqual(self.updater.sync(True), "disabled")
            self.updater.change(enabled=True, last_checked=update.now())
            self.assertEqual(self.updater.sync(), "not-due")
            latest.assert_not_called()

    def test_network_failure_is_recorded_and_current_remains_usable(self):
        with mock.patch.object(update, "latest_release", side_effect=TimeoutError("offline")):
            self.assertEqual(self.updater.sync(True), "failed")
        state = self.updater.state()
        self.assertEqual(state["current"], self.current)
        self.assertEqual(state["last_error"], "offline")
        self.assertIsNone(state["failed_tag"])

    def test_network_failure_during_candidate_download_can_retry_next_cycle(self):
        with mock.patch.object(update, "latest_release", return_value="v1.1.0"):
            with mock.patch.object(self.updater, "build_candidate", side_effect=TimeoutError("download timeout")):
                self.assertEqual(self.updater.sync(True), "failed")
        self.assertIsNone(self.updater.state()["failed_tag"])

    def test_missing_math_test_evidence_stops_before_full_build(self):
        manifest = self.root / "candidate.json"
        patch = self.root / "candidate.patch"
        patch.write_bytes(b"patch fixture")
        update.atomic_json(manifest, {"rust_toolchain": "1.98.1", "zed": {"tag": "v1.1.0", "commit": "b" * 40},
                                      "patch": {"file": str(patch)}})
        with mock.patch.object(self.updater, "prepare", return_value=(self.root, manifest)):
            with mock.patch.object(self.updater, "build_action", return_value="test result: ok. 5 passed; 0 failed;") as build:
                with self.assertRaisesRegex(update.UpdateError, "evidence"):
                    self.updater.build_candidate("v1.1.0", self.updater.state())
        self.assertEqual([call.args[0] for call in build.call_args_list], ["test"])

    def test_native_test_evidence_is_reused_only_for_unchanged_candidate(self):
        manifest = self.root / "candidate.json"
        patch = self.root / "candidate.patch"
        patch.write_bytes(b"tested patch")
        update.atomic_json(manifest, {"rust_toolchain": "1.98.1", "zed": {"tag": "v1.1.0", "commit": "b" * 40},
                                      "patch": {"file": str(patch)}})
        output = ("test katex::tests::test_math_with_setext_lines_renders_to_svg ... ok\n"
                  "test result: ok. 217 passed; 0 failed;")
        with mock.patch.object(self.updater, "build_action", return_value=output) as build:
            self.assertEqual(self.updater.native_tests(self.root, manifest, {}), 217)
            build.reset_mock()
            with mock.patch.object(update.subprocess, "check_output", return_value=b"tested patch"):
                self.assertEqual(self.updater.native_tests(self.root, manifest, {}), 217)
                build.assert_not_called()
            patch.write_bytes(b"changed patch")
            self.assertEqual(self.updater.native_tests(self.root, manifest, {}), 217)
            build.assert_called_once()

    def test_transient_https_failure_retries_with_backoff_without_weakening_tls(self):
        response = object()
        with mock.patch.object(update.urllib.request, "urlopen", side_effect=[update.urllib.error.URLError("TLS EOF"), response]) as request:
            with mock.patch.object(update.time, "sleep") as sleep:
                self.assertIs(update.open_url("https://api.github.com/test", 30), response)
        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(3)
        self.assertEqual(request.call_args.kwargs, {"timeout": 30})

    def test_only_exit_allows_activation_and_preserves_rollback(self):
        self.updater.change(pending=self.candidate)
        self.assertEqual(self.updater.promote(True)["current"], self.current)
        state = self.updater.promote(False)
        self.assertEqual(state["current"], self.candidate)
        self.assertEqual(state["previous"], self.current)
        self.assertIsNone(state["pending"])

    def test_disable_prevents_pending_activation_and_other_build_state_survives_switch(self):
        self.updater.change(enabled=False, pending=self.candidate)
        self.assertEqual(self.updater.promote(False)["current"], self.current)
        self.updater.change(enabled=True, phase="building", building_tag="v1.2.0")
        state = self.updater.promote(False)
        self.assertEqual(state["current"], self.candidate)
        self.assertEqual(state["phase"], "building")
        self.assertEqual(state["building_tag"], "v1.2.0")

    def test_activation_copies_database_before_switch_without_changing_original(self):
        database = self.root / "profile/Zed/db/0-stable"
        database.mkdir(parents=True)
        (database / "db.sqlite").write_bytes(b"pre-migration database fixture")
        self.updater.change(pending=self.candidate)
        state = self.updater.promote(False)
        snapshot = Path(state["last_database_backup"]) / "0-stable/db.sqlite"
        self.assertEqual(snapshot.read_bytes(), (database / "db.sqlite").read_bytes())

    def test_rollback_waits_for_exit_and_holds_rejected_release(self):
        self.updater.change(current=self.candidate, previous=self.current)
        with self.assertRaisesRegex(update.UpdateError, "exit"):
            self.updater.rollback(True)
        self.updater.rollback(False)
        state = self.updater.state()
        self.assertEqual(state["current"], self.current)
        self.assertEqual(state["held_tag"], "v1.1.0")
        with mock.patch.object(update, "latest_release", return_value="v1.1.0"):
            with mock.patch.object(self.updater, "build_candidate") as build:
                self.assertEqual(self.updater.sync(True), "held-after-rollback")
                build.assert_not_called()

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

    def test_windows_run_resolves_tools_using_child_path(self):
        tools = self.root / "tools"
        tools.mkdir()
        executable = tools / "local-tool.exe"
        executable.write_bytes(b"path lookup only")
        environment = {"PATH": str(tools), "PATHEXT": ".EXE"}
        with mock.patch.object(update.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, b"ok")) as run:
            self.assertEqual(update.run(["local-tool", "--version"], environment=environment), "ok")
        self.assertEqual(Path(run.call_args.args[0][0]), executable)

    def test_structured_commands_keep_progress_out_of_json(self):
        output = update.run([sys.executable, "-c",
                             "import sys; print('download progress',file=sys.stderr); print('{\"ok\":true}')"],
                            separate_stderr=True)
        self.assertTrue(json.loads(output)["ok"])

    def test_package_smoke_checks_cli_identity_and_actual_gui_entry(self):
        recipe = {"zed": {"tag": "v1.1.0", "commit": "b" * 40}}
        directory = Path(self.candidate["directory"])
        with mock.patch.object(self.updater, "command", side_effect=[
            "Usage: Zed [PATHS_OR_URLS]...", "Zed 1.1.0 " + "b" * 40 + " - " + str(directory / "Zed.exe"),
        ]):
            self.updater.validate_package(directory, recipe)
        with mock.patch.object(self.updater, "command", side_effect=["PATHS_OR_URLS", "Zed 1.0.0 wrong.exe"]):
            with self.assertRaises(update.UpdateError):
                self.updater.validate_package(directory, recipe)

    def test_real_three_way_update_preserves_upstream_code_and_lockfile(self):
        repository = self.updater.repository
        repository.mkdir(parents=True)

        def git(*args):
            return update.run(["git", *args], cwd=repository)

        git("init")
        git("config", "user.name", "Update integration test")
        git("config", "user.email", "update-test@example.invalid")
        git("remote", "add", "origin", update.REPOSITORY)
        lines = ["line " + str(number) + "\n" for number in range(30)]
        (repository / "renderer.rs").write_text("".join(lines), encoding="utf-8")
        (repository / "Cargo.lock").write_text("base lock\n", encoding="utf-8")
        (repository / "rust-toolchain.toml").write_text('[toolchain]\nchannel = "1.98.1"\n', encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "upstream base")
        base_commit = git("rev-parse", "HEAD")
        lines[10] = "native math\n"
        (repository / "renderer.rs").write_text("".join(lines), encoding="utf-8")
        (repository / "Cargo.lock").write_text("obsolete patched lock\n", encoding="utf-8")
        patch = self.root / "native.patch"
        patch.write_bytes(subprocess.check_output(["git", "diff", "--binary"], cwd=repository))
        git("restore", "renderer.rs", "Cargo.lock")
        lines[10] = "line 10\n"
        lines[12] = "new upstream behavior\n"
        (repository / "renderer.rs").write_text("".join(lines), encoding="utf-8")
        (repository / "Cargo.lock").write_text("new official lock\n", encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "new official release")
        git("tag", "v1.1.0")
        commit = git("rev-parse", "HEAD")
        recipe = {"zed": {"tag": "v1.0.0", "commit": base_commit}, "patch": {"file": str(patch)},
                  "verification": {"old_macos_results": True}, "rust_toolchain": "1.98.1"}
        update.atomic_json(Path(self.current["manifest"]), recipe)
        update.atomic_json(self.root / "manifest.json", {"windows_update": {}})
        actual = self.updater.command
        cargo_calls = []

        def command(arguments, cwd=None, environment=None):
            if arguments[0] == "git" and arguments[1] == "fetch":
                return ""
            if arguments[0] == "rustup":
                return "toolchain fixture"
            if arguments[0] == "cargo":
                self.assertEqual((cwd / "Cargo.lock").read_text(), "new official lock\n")
                cargo_calls.append(True)
                if len(cargo_calls) == 1:
                    raise update.UpdateError("temporary download failure")
                (cwd / "Cargo.lock").write_text("new official lock plus math\n", encoding="utf-8")
                return ""
            return actual(arguments, cwd, environment)

        with mock.patch.object(self.updater, "command", side_effect=command):
            with self.assertRaisesRegex(update.UpdateError, "download"):
                self.updater.prepare("v1.1.0", self.updater.state())
            # Resume from the completed patch step, without resetting or applying twice.
            source, manifest_path = self.updater.prepare("v1.1.0", self.updater.state())
        prepared = update.read_json(manifest_path)
        self.assertEqual(prepared["zed"], {"tag": "v1.1.0", "commit": commit})
        self.assertNotIn("verification", prepared)
        self.assertEqual((source / "Cargo.lock").read_text(), "new official lock plus math\n")
        self.assertIn("native math", (source / "renderer.rs").read_text())
        self.assertIn("new upstream behavior", (source / "renderer.rs").read_text())
        self.assertEqual(git("status", "--porcelain"), "")
        update.run(["git", "apply", "--reverse", "--check", prepared["patch"]["file"]], cwd=source)
        lines[20] = "next upstream release\n"
        (repository / "renderer.rs").write_text("".join(lines), encoding="utf-8")
        git("add", ".")
        git("commit", "-m", "next release")
        git("tag", "v1.2.0")
        update.atomic_json(self.root / "manifest.json", {"windows_update": {"seed": {
            "tag": "v1.1.0", "commit": commit, "patch": prepared["patch"]["file"],
        }}})
        with mock.patch.object(self.updater, "command", side_effect=command):
            next_source, _ = self.updater.prepare("v1.2.0", self.updater.state())
        self.assertIn("native math", (next_source / "renderer.rs").read_text())
        self.assertIn("next upstream release", (next_source / "renderer.rs").read_text())
        self.assertEqual(git("status", "--porcelain"), "")

    def test_official_webrtc_download_uses_supported_custom_cache_and_reuses_it(self):
        helper = self.root / "webrtc-helper/src/lib.rs"
        helper.parent.mkdir(parents=True)
        helper.write_text('pub const WEBRTC_TAG: &str = "webrtc-abc123-4";', encoding="utf-8")
        metadata = {"packages": [{"name": "webrtc-sys-build", "manifest_path": str(helper.parent.parent / "Cargo.toml")}]}
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as bundle:
            bundle.writestr("win-x64-release/lib/webrtc.lib", "official binary fixture")
            bundle.writestr("win-x64-release/include/api/peer_connection_interface.h", "header fixture")
        data.seek(0)
        with mock.patch.object(update, "run", return_value=json.dumps(metadata)):
            with mock.patch.object(update.urllib.request, "urlopen", return_value=data) as download:
                first = {}
                self.updater.prepare_webrtc(self.root, first)
                second = {}
                self.updater.prepare_webrtc(self.root, second)
        self.assertEqual(first["LK_CUSTOM_WEBRTC"], second["LK_CUSTOM_WEBRTC"])
        download.assert_called_once()
        self.assertTrue(download.call_args.args[0].startswith("https://github.com/zed-industries/livekit-rust-sdks/"))


if __name__ == "__main__":
    unittest.main()
