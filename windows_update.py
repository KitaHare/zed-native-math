#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Follow official Zed stable releases while retaining the native math patch."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
import zipfile

from build_recipe import default_recipe


ROOT = Path(__file__).resolve().parent
REPOSITORY = "https://github.com/zed-industries/zed.git"
RELEASE_API = "https://api.github.com/repos/zed-industries/zed/releases/latest"
INTERVAL_SECONDS = 30 * 60


class UpdateError(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def version(tag):
    if not isinstance(tag, str) or not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise UpdateError("Unexpected stable release tag: " + str(tag))
    return tuple(map(int, tag[1:].split(".")))


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp-" + str(os.getpid()))
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def lock(path, blocking=True):
    """OS-owned lock: a crashed worker cannot leave a stale PID lock behind."""
    import msvcrt
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        if not stream.tell():
            stream.write(b"\0")
            stream.flush()
        acquired = False
        while not acquired:
            stream.seek(0)
            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                if not blocking:
                    break
                time.sleep(0.05)
        try:
            yield acquired
        finally:
            if acquired:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def run(command, cwd=None, environment=None, log=None, separate_stderr=False):
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    command = [str(arg) for arg in command]
    if environment:
        executable = shutil.which(command[0], path=environment.get("PATH"))
        if not executable:
            raise UpdateError("Required tool not found in the update environment: " + command[0])
        command[0] = executable
    if log:
        with log.open("a", encoding="utf-8") as stream:
            stream.write("\n$ " + subprocess.list2cmdline(command) + "\n")
    result = subprocess.run(command, cwd=cwd, env=environment,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE if separate_stderr else subprocess.STDOUT,
                            creationflags=flags)
    output = result.stdout.decode("utf-8", errors="replace")
    diagnostics = (result.stderr or b"").decode("utf-8", errors="replace")
    if log:
        with log.open("a", encoding="utf-8") as stream:
            stream.write(output + diagnostics)
    if result.returncode:
        raise UpdateError("Command failed (" + str(result.returncode) + "): "
                          + subprocess.list2cmdline([str(arg) for arg in command])
                          + "\n" + (output + diagnostics)[-4000:])
    return output.strip()


def latest_release():
    request = urllib.request.Request(RELEASE_API, headers={
        "User-Agent": "Zed-Native-Math-Windows-Updater", "Accept": "application/vnd.github+json",
    })
    with open_url(request, timeout=30) as response:
        data = json.load(response)
    version(data.get("tag_name"))
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("The official endpoint did not return a stable release")
    return data["tag_name"]


def open_url(request, timeout):
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def rust_channel(source):
    text = (source / "rust-toolchain.toml").read_text(encoding="utf-8")
    match = re.search(r'^channel\s*=\s*"(\d+\.\d+\.\d+)"', text, re.MULTILINE)
    if not match:
        raise UpdateError("Cannot read the official pinned Rust toolchain")
    return match.group(1)


def stable_is_running():
    # Custom data directories are independent instances (including our smoke window).
    output = run(["powershell.exe", "-NoProfile", "-Command",
                  "@(Get-CimInstance Win32_Process -Filter \"Name='Zed.exe'\" | "
                  "Select-Object CommandLine) | ConvertTo-Json -Compress"])
    processes = json.loads(output or "[]")
    if isinstance(processes, dict):
        processes = [processes]
    return any("--user-data-dir" not in (item.get("CommandLine") or "")
               and "--crash-handler" not in (item.get("CommandLine") or "")
               for item in processes)


class Updater:
    def __init__(self, root=ROOT):
        self.root = root
        self.work = root / ".work/windows-updates"
        self.state_path = self.work / "state.json"
        self.repository = root / ".work/zed-upstream"
        self.log = self.work / "update.log"

    def state(self):
        if not self.state_path.is_file():
            raise UpdateError("Run install-windows-updates.ps1 first")
        return read_json(self.state_path)

    def change(self, **fields):
        with lock(self.work / "state.lock"):
            state = self.state()
            state.update(fields)
            atomic_json(self.state_path, state)
            return state

    def initialize(self):
        if self.state_path.exists():
            self.change(enabled=True)
            return
        package = self.root / "dist/Zed Native Math (Community)"
        if not (package / "Zed.exe").is_file():
            raise UpdateError("Build and package the initial community application first")
        manifest = read_json(package / "native-math-manifest.json")
        catalog = read_json(self.root / "manifest.json")
        recipe = default_recipe(catalog, "win32")
        # A user may have packaged the old Windows baseline before installing
        # the updater. Bootstrap it as that release, never as the new seed.
        if manifest["zed"] != recipe["zed"]:
            if manifest["zed"] != catalog["zed"]:
                raise UpdateError("Initial package does not match a supported Windows build revision")
            recipe = catalog
        recipe["patch"]["file"] = str((self.root / recipe["patch"]["file"]).resolve())
        recipe_path = self.work / "initial-manifest.json"
        atomic_json(recipe_path, recipe)
        atomic_json(self.state_path, {
            "enabled": True, "channel": "stable", "check_interval_seconds": INTERVAL_SECONDS,
            "current": {"tag": manifest["zed"]["tag"], "commit": manifest["zed"]["commit"],
                        "directory": str(package), "manifest": str(recipe_path)},
            "previous": None, "pending": None, "phase": "installed", "last_checked": None,
        })

    def environment(self):
        environment = os.environ.copy()
        cargo = self.root / ".work/cargo"
        if (cargo / "bin/rustup.exe").is_file():
            environment["CARGO_HOME"] = str(cargo)
            environment["RUSTUP_HOME"] = str(self.root / ".work/rustup")
            environment["PATH"] = str(cargo / "bin") + os.pathsep + environment["PATH"]
        environment["CARGO_TARGET_DIR"] = str(self.repository / "target")
        environment["CARGO_INCREMENTAL"] = "0"
        environment["CARGO_NET_GIT_FETCH_WITH_CLI"] = "true"
        environment["CARGO_HTTP_MULTIPLEXING"] = "false"
        environment.pop("CARGO_BUILD_TARGET", None)
        environment.pop("RUSTUP_TOOLCHAIN", None)
        return environment

    def command(self, command, cwd=None, environment=None):
        return run(command, cwd, environment, self.log)

    def ensure_toolchain(self, channel, environment):
        target = channel + "-x86_64-pc-windows-msvc"
        installed = self.command(["rustup", "toolchain", "list"], environment=environment)
        if not any(line.split()[:1] == [target] for line in installed.splitlines()):
            self.command(["rustup", "toolchain", "install", target, "--profile", "minimal", "--no-self-update"],
                         environment=environment)

    def build_action(self, action, source, manifest, environment, destination=None):
        command = ["powershell.exe", "-NoProfile", "-File", self.root / "build-windows.ps1",
                   action, "-Source", source, "-Manifest", manifest]
        if destination:
            command.extend(["-Destination", destination])
        return self.command(command, environment=environment)

    def prepare(self, tag, state):
        recipe = read_json(Path(state["current"]["manifest"]))
        baseline_patch = Path(recipe["patch"]["file"])
        seed = read_json(self.root / "manifest.json").get("windows_update", {}).get("seed")
        use_seed = seed and version(recipe["zed"]["tag"]) < version(seed["tag"]) <= version(tag)
        exact_seed = use_seed and tag == seed["tag"]
        if use_seed:
            baseline_patch = self.root / seed["patch"]
        if not self.repository.exists():
            self.command(["git", "clone", "--depth", "1", "--branch", recipe["zed"]["tag"],
                          "--single-branch", REPOSITORY, self.repository])
        remote = self.command(["git", "remote", "get-url", "origin"], self.repository)
        if remote != REPOSITORY:
            raise UpdateError("Update checkout origin must be " + REPOSITORY)
        if use_seed:
            self.command(["git", "fetch", "--depth", "1", "origin", "tag", seed["tag"]], self.repository)
            seed_commit = self.command(["git", "rev-parse", "refs/tags/" + seed["tag"] + "^{commit}"], self.repository)
            if seed_commit != seed["commit"]:
                raise UpdateError("The maintained update patch base does not match its official tag")
        self.command(["git", "fetch", "--depth", "1", "origin", "tag", tag], self.repository)
        commit = self.command(["git", "rev-parse", "refs/tags/" + tag + "^{commit}"], self.repository)
        # A maintained rebase is a different input from the patch that conflicted.
        # Keep its checkout and evidence separate; never discard a manual resolution.
        build_id = f"{tag}-from-{seed['tag']}" if use_seed else tag
        source = self.root / ".work/update-sources" / build_id
        candidate = self.work / "candidates" / build_id
        manifest_path = candidate / "manifest.json"
        preparation_path = candidate / "preparation.json"
        if manifest_path.is_file():
            existing = read_json(manifest_path)
            if existing["zed"] != {"tag": tag, "commit": commit}:
                raise UpdateError("Prepared candidate does not match the official tag")
            self.build_action("verify", source, manifest_path, self.environment())
            return source, manifest_path
        if preparation_path.is_file():
            preparation = read_json(preparation_path)
            if preparation != {"commit": commit, "patch": str(baseline_patch)}:
                raise UpdateError("Prepared source belongs to a different update")
            if self.command(["git", "rev-parse", "HEAD"], source) != commit:
                raise UpdateError("Prepared source revision changed")
        else:
            if source.exists():
                raise UpdateError("Incomplete candidate checkout retained at " + str(source)
                                  + "; inspect/resolve its patch before retrying")
            source.parent.mkdir(parents=True, exist_ok=True)
            self.command(["git", "worktree", "add", "--detach", source, commit], self.repository)
            if exact_seed:
                self.command(["git", "apply", "--index", baseline_patch], source)
            else:
                # A different upstream version must keep its own dependency choices.
                self.command(["git", "apply", "--3way", "--index", "--exclude=Cargo.lock", baseline_patch], source)
            atomic_json(preparation_path, {"commit": commit, "patch": str(baseline_patch)})
        environment = self.environment()
        channel = rust_channel(source)
        self.ensure_toolchain(channel, environment)
        environment["RUSTUP_TOOLCHAIN"] = channel
        if not exact_seed:
            self.command(["cargo", "update", "--workspace"], source, environment)
        candidate.mkdir(parents=True, exist_ok=True)
        patch = candidate / "native-math.patch"
        # Binary-safe output; Git object IDs are not custom file-integrity checks.
        diff = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=source,
                                       creationflags=subprocess.CREATE_NO_WINDOW)
        patch.write_bytes(diff)
        recipe["zed"] = {"tag": tag, "commit": commit}
        recipe["patch"] = {"file": str(patch)}
        recipe["rust_toolchain"] = channel
        recipe.pop("verification", None)
        recipe["tested_platforms"] = ["Windows x64"]
        recipe["windows_verification"] = {"status": "building"}
        recipe["automatic_updater_included"] = True
        atomic_json(manifest_path, recipe)
        return source, manifest_path

    def validate_package(self, directory, recipe):
        output = self.command([directory / "Zed.exe", "--help"])
        if "PATHS_OR_URLS" not in output:
            raise UpdateError("Packaged GUI command entry did not start correctly")
        output = self.command([directory / "bin/zed.exe", "--version"])
        if ("Zed " + recipe["zed"]["tag"][1:] + " " not in output
                or recipe["zed"]["commit"] not in output
                or str(directory).lower() not in output.lower()):
            raise UpdateError("Packaged CLI does not resolve the expected GUI: " + output)

    def prepare_webrtc(self, source, environment):
        if environment.get("LK_CUSTOM_WEBRTC"):
            return
        metadata = json.loads(run(["cargo", "metadata", "--locked", "--format-version", "1",
                                   "--filter-platform", "x86_64-pc-windows-msvc"], source, environment,
                                  separate_stderr=True))
        package = next((item for item in metadata["packages"] if item["name"] == "webrtc-sys-build"), None)
        if package is None:
            return
        helper = Path(package["manifest_path"]).parent / "src/lib.rs"
        match = re.search(r'pub const WEBRTC_TAG: &str = "(webrtc-[a-f0-9]+-\d+)"',
                          helper.read_text(encoding="utf-8"))
        if not match:
            raise UpdateError("The official WebRTC build helper changed; review its download contract")
        tag = match.group(1)
        cache = self.work / "webrtc" / tag
        directory = cache / "win-x64-release"
        ready = cache / "ready.json"
        if not (ready.is_file() and (directory / "lib/webrtc.lib").is_file()
                and (directory / "include/api/peer_connection_interface.h").is_file()):
            cache.mkdir(parents=True, exist_ok=True)
            archive = cache / "webrtc-win-x64-release.zip"
            url = ("https://github.com/zed-industries/livekit-rust-sdks/releases/download/"
                   + tag + "/webrtc-win-x64-release.zip")
            self.change(phase="downloading-webrtc")
            with self.log.open("a", encoding="utf-8") as log:
                log.write("\nDownloading official WebRTC with system Python TLS: " + url + "\n")
            with open_url(url, timeout=90) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    if not (cache / member.filename).resolve().is_relative_to(cache.resolve()):
                        raise UpdateError("WebRTC archive contains an invalid extraction path")
                bundle.extractall(cache)
            if not (directory / "lib/webrtc.lib").is_file():
                raise UpdateError("The official WebRTC archive layout changed")
            atomic_json(ready, {"tag": tag, "source": url})
        # Supported upstream override; keeps the TLS-valid download cached across builds.
        environment["LK_CUSTOM_WEBRTC"] = str(directory)

    def build_candidate(self, tag, state):
        source, manifest_path = self.prepare(tag, state)
        environment = self.environment()
        self.change(phase="testing", building_tag=tag)
        passed = self.native_tests(source, manifest_path, environment)
        environment["RUSTUP_TOOLCHAIN"] = read_json(manifest_path)["rust_toolchain"]
        self.prepare_webrtc(source, environment)
        self.change(phase="building")
        self.build_action("build", source, manifest_path, environment)
        recipe = read_json(manifest_path)
        recipe["windows_verification"] = {
            "native_markdown_tests_passed": passed, "math_svg_decode_test_passed": True,
            "native_application_build_status": "passed-debug-gui-and-cli",
            "application_package_status": "passed-version-smoke",
        }
        atomic_json(manifest_path, recipe)
        directory = self.root / "dist/windows-versions" / (tag + "-" + str(time.time_ns()))
        self.change(phase="packaging")
        self.build_action("package", source, manifest_path, environment, directory)
        self.validate_package(directory, recipe)
        return {"tag": tag, "commit": recipe["zed"]["commit"],
                "directory": str(directory), "manifest": str(manifest_path)}

    def native_tests(self, source, manifest_path, environment):
        recipe = read_json(manifest_path)
        evidence_path = manifest_path.parent / "native-tests.json"
        tested_patch = manifest_path.parent / "native-tests.patch"
        patch = Path(recipe["patch"]["file"]).read_bytes()
        identity = {"zed": recipe["zed"], "toolchain": recipe["rust_toolchain"]}
        if evidence_path.is_file() and tested_patch.is_file() and tested_patch.read_bytes() == patch:
            evidence = read_json(evidence_path)
            actual = subprocess.check_output(["git", "diff", "--binary", "HEAD"], cwd=source,
                                             creationflags=subprocess.CREATE_NO_WINDOW)
            if evidence.get("identity") == identity and actual == patch:
                return evidence["passed"]
        tests = self.build_action("test", source, manifest_path, environment)
        match = re.search(r"test result: ok\. (\d+) passed; 0 failed;", tests)
        if not match or "test katex::tests::test_math_with_setext_lines_renders_to_svg ... ok" not in tests:
            raise UpdateError("Native math/Markdown test evidence is missing")
        passed = int(match.group(1))
        tested_patch.write_bytes(patch)
        atomic_json(evidence_path, {"identity": identity, "passed": passed, "tested_at": now()})
        return passed

    def sync(self, force=False):
        with lock(self.work / "update.lock", blocking=False) as acquired:
            if not acquired:
                return "already-running"
            state = self.state()
            if not state["enabled"]:
                return "disabled"
            checked = state.get("last_checked")
            if not force and checked and time.time() - datetime.fromisoformat(checked).timestamp() < INTERVAL_SECONDS:
                return "not-due"
            tag = None
            try:
                tag = latest_release()
                self.change(last_checked=now(), latest_tag=tag, last_error=None, last_error_kind=None)
                installed = state.get("pending") or state["current"]
                if version(tag) <= version(installed["tag"]):
                    self.change(phase="ready" if state.get("pending") else "up-to-date")
                    return "up-to-date"
                if state.get("held_tag") == tag:
                    self.change(phase="rolled-back")
                    return "held-after-rollback"
                # Do not retry an unchanged patch/compiler failure every 30 minutes.
                if state.get("failed_tag") == tag and not force:
                    self.change(phase="failed", last_error=state.get("failure_detail"), last_error_kind="update")
                    return "failed-awaiting-retry"
                self.change(phase="preparing", building_tag=tag, failed_tag=None, failure_detail=None)
                candidate = self.build_candidate(tag, state)
                self.change(pending=candidate, phase="ready", building_tag=None,
                            failed_tag=None, failure_detail=None, last_error=None, ready_at=now())
                return "ready"
            except Exception as error:
                retry_network = isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))
                checked_at = now()
                detail = str(error)
                kind = "network" if retry_network else "update"
                fields = dict(phase="failed", last_checked=checked_at, last_error=detail,
                              last_error_kind=kind, building_tag=None)
                # An interrupted release check must not forget a failed candidate.
                if not retry_network:
                    fields.update(failed_tag=tag, failure_detail=detail)
                self.change(**fields)
                stage = f"updating {tag}" if tag else f"checking {RELEASE_API}"
                with self.log.open("a", encoding="utf-8") as log:
                    log.write(f"\n[{checked_at}] {kind} error while {stage}: {detail}\n")
                return "failed"

    def promote(self, running):
        with lock(self.work / "state.lock"):
            state = self.state()
            if state["enabled"] and state.get("pending") and not running:
                state["last_database_backup"] = self.backup_database()
                state["previous"] = state["current"]
                state["current"] = state.pop("pending")
                state["pending"] = None
                state["activated_at"] = now()
                if not state.get("building_tag"):
                    state["phase"] = "up-to-date"
                atomic_json(self.state_path, state)
            return state

    def backup_database(self):
        database = Path(os.environ["LOCALAPPDATA"]) / "Zed/db"
        if not database.exists():
            return None
        destination = self.work / "database-backups" / str(time.time_ns())
        # Only after stable exits: keep a pre-migration snapshot for data recovery.
        shutil.copytree(database, destination)
        return str(destination)

    def rollback(self, running):
        if running:
            raise UpdateError("Save and fully exit stable Zed before rolling back")
        with lock(self.work / "state.lock"):
            state = self.state()
            if not state.get("previous"):
                raise UpdateError("No previous managed version is available")
            current = state["current"]
            state["current"], state["previous"] = state["previous"], current
            state.update(pending=None, held_tag=current["tag"], phase="rolled-back")
            atomic_json(self.state_path, state)

    def launch(self, arguments):
        state = self.promote(stable_is_running())
        current = state["current"]
        binary = Path(current["directory"]) / "Zed.exe"
        if not binary.is_file():
            raise UpdateError("Active application is missing; restore the shortcut backup or roll back")
        environment = os.environ.copy()
        environment.pop("ZED_STATELESS", None)
        subprocess.Popen([str(binary), *arguments], cwd=binary.parent, env=environment,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if state["enabled"]:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            subprocess.Popen([str(pythonw), str(self.root / "windows_update.py"), "sync"],
                             cwd=self.root, creationflags=subprocess.CREATE_NO_WINDOW,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        failure_key = state.get("failed_tag") or state.get("last_checked")
        if (state.get("last_error") and state.get("last_error_kind") != "network"
                and state.get("notified_failure") != failure_key):
            self.change(notified_failure=failure_key)
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                "官方更新未完成，继续使用现有数学版。\n\n"
                + str(state["last_error"])[-1200:] + "\n\n日志：" + str(self.log),
                "Zed Native Math 更新未完成", 0x30)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["initialize", "sync", "launch", "status", "rollback", "disable"])
    parser.add_argument("--force", action="store_true", help="check now and retry a failed candidate")
    arguments, forwarded = parser.parse_known_args()
    if os.name != "nt":
        parser.error("This updater runs on Windows only")
    updater = Updater()
    try:
        if arguments.action == "initialize":
            updater.initialize()
        elif arguments.action == "sync":
            result = updater.sync(arguments.force)
            print(result)
            return 1 if result == "failed" else 0
        elif arguments.action == "launch":
            updater.launch(forwarded)
        elif arguments.action == "status":
            print(json.dumps(updater.state(), ensure_ascii=False, indent=2))
        elif arguments.action == "disable":
            updater.change(enabled=False)
        elif arguments.action == "rollback":
            updater.rollback(stable_is_running())
        return 0
    except Exception as error:
        if sys.stderr:
            print("Windows update stopped: " + str(error), file=sys.stderr)
        if arguments.action == "launch":
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, str(error), "Zed Native Math 启动失败", 0x10)
        return 1


if __name__ == "__main__":
    sys.exit(main())
