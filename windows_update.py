#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lightweight Windows updater that installs prebuilt Zed Native Math releases."""

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile


ROOT = Path(__file__).resolve().parent
DEFAULT_RELEASE_REPO = "KitaHare/zed-native-math"
RELEASE_API_TEMPLATE = "https://api.github.com/repos/{repo}/releases/latest"
INTERVAL_SECONDS = 6 * 60 * 60
MAX_DATABASE_BACKUPS = 3
WINDOWS_ASSET_SUFFIX = "-windows-x64.zip"
REQUIRED_PACKAGE_FILES = (
    "Zed.exe",
    "bin/zed.exe",
    "conpty.dll",
    "OpenConsole.exe",
    "native-math-manifest.json",
)


class UpdateError(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def version(tag):
    if not isinstance(tag, str):
        raise UpdateError("Unexpected release tag: " + str(tag))
    match = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)(?:-math\.(\d+))?", tag)
    if not match:
        raise UpdateError("Unexpected stable release tag: " + str(tag))
    major, minor, patch, revision = match.groups()
    return (int(major), int(minor), int(patch), int(revision or 0))


def base_zed_tag(tag):
    major, minor, patch, _ = version(tag)
    return f"v{major}.{minor}.{patch}"


def release_api_url(repo=DEFAULT_RELEASE_REPO):
    return RELEASE_API_TEMPLATE.format(repo=repo)


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


def open_url(request, timeout):
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def latest_release(repo=DEFAULT_RELEASE_REPO):
    api_url = release_api_url(repo)
    request = urllib.request.Request(api_url, headers={
        "User-Agent": "Zed-Native-Math-Windows-Updater",
        "Accept": "application/vnd.github+json",
    })
    with open_url(request, timeout=30) as response:
        data = json.load(response)
    tag = data.get("tag_name")
    version(tag)
    if data.get("draft") or data.get("prerelease"):
        raise UpdateError("The release endpoint did not return a stable release")
    assets = data.get("assets") or []
    expected_name = f"zed-native-math-{tag}{WINDOWS_ASSET_SUFFIX}"
    asset = next((item for item in assets if item.get("name") == expected_name), None)
    if asset is None:
        asset = next((item for item in assets
                      if isinstance(item.get("name"), str)
                      and item["name"].endswith(WINDOWS_ASSET_SUFFIX)), None)
    if asset is None:
        raise UpdateError(f"Release {tag} does not include a Windows x64 package ({expected_name})")
    download_url = asset.get("browser_download_url") or ""
    if not download_url.startswith("https://"):
        raise UpdateError(f"Release {tag} asset download URL must use HTTPS")
    return {
        "tag": tag,
        "asset_name": asset["name"],
        "download_url": download_url,
    }


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


def directory_size(path):
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root_dir, _, filenames in os.walk(path):
        for filename in filenames:
            try:
                total += (Path(root_dir) / filename).stat().st_size
            except OSError:
                pass
    return total


def rmtree_force(path):
    """Remove a directory tree on Windows, clearing read-only attributes (e.g. Git packfiles)."""
    def handle_error(func, target, _exc):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError:
            pass
    shutil.rmtree(path, onerror=handle_error)


def extract_zip_safely(archive_path, destination):
    destination.mkdir(parents=True, exist_ok=True)
    resolved_root = destination.resolve()
    with zipfile.ZipFile(archive_path) as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(resolved_root):
                raise UpdateError("Release archive contains an invalid extraction path")
        bundle.extractall(destination)
    # Support archives wrapped in a single top-level directory as well as flat archives.
    if not (destination / "Zed.exe").is_file():
        entries = list(destination.iterdir())
        if len(entries) == 1 and entries[0].is_dir() and (entries[0] / "Zed.exe").is_file():
            nested = entries[0]
            for child in list(nested.iterdir()):
                shutil.move(str(child), str(destination / child.name))
            nested.rmdir()


class Updater:
    def __init__(self, root=ROOT):
        self.root = root
        self.work = root / ".work/windows-updates"
        self.state_path = self.work / "state.json"
        self.log = self.work / "update.log"

    def release_repository(self):
        override = os.environ.get("ZED_NATIVE_MATH_RELEASE_REPO")
        if override:
            return override
        if self.state_path.is_file():
            repo = read_json(self.state_path).get("repository")
            if repo:
                return repo
        manifest_path = self.root / "manifest.json"
        if manifest_path.is_file():
            repo = read_json(manifest_path).get("windows_update", {}).get("repository")
            if repo:
                return repo
        return DEFAULT_RELEASE_REPO

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

    def write_cli_shims(self):
        """Create stable dist/bin/zed shims that always delegate to the active version."""
        bin_dir = self.root / "dist/bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        python_exe = Path(sys.executable)
        if python_exe.name.lower() == "pythonw.exe":
            console_python = python_exe.with_name("python.exe")
            if console_python.is_file():
                python_exe = console_python
        worker = self.root / "windows_update.py"
        # Use %~dp0 so cmd.exe resolves non-ASCII repo paths cleanly under GBK/CP936 as well as UTF-8.
        cmd_script = (
            "@echo off\r\n"
            f'"{python_exe}" "%~dp0..\\..\\windows_update.py" cli %*\r\n'
        )
        (bin_dir / "zed.cmd").write_text(cmd_script, encoding="utf-8")
        sh_script = (
            "#!/usr/bin/env sh\n"
            f'exec "{python_exe.as_posix()}" "{worker.as_posix()}" cli "$@"\n'
        )
        (bin_dir / "zed").write_bytes(sh_script.encode("utf-8"))
        return bin_dir

    def refresh_shell_icons(self, active_directory):
        """Keep HKCU context-menu and open-with icons pointed at the active Zed.exe."""
        if os.name != "nt":
            return
        icon_value = str(Path(active_directory) / "Zed.exe") + ",0"
        try:
            import winreg
        except ImportError:
            return
        shell_keys = (
            r"Software\Classes\*\shell\ZedNativeMath",
            r"Software\Classes\Directory\shell\ZedNativeMath",
            r"Software\Classes\Directory\Background\shell\ZedNativeMath",
        )
        for subkey in shell_keys:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, icon_value)
            except OSError:
                pass
        for subkey in (
            r"Software\Classes\Applications\Zed.exe\DefaultIcon",
            r"Software\Classes\zed\DefaultIcon",
        ):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.SetValueEx(key, "", 0, winreg.REG_SZ, icon_value)
            except OSError:
                pass

    def initialize(self):
        repo = self.release_repository()
        if self.state_path.exists():
            with lock(self.work / "state.lock"):
                state = self.state()
                migrating_from_source = state.get("mode") != "release"
                state["enabled"] = True
                state["mode"] = "release"
                state["repository"] = repo
                state["check_interval_seconds"] = INTERVAL_SECONDS
                if migrating_from_source:
                    # Clear obsolete local patch-conflict errors when moving to cloud releases.
                    state["failed_tag"] = None
                    state["failure_detail"] = None
                    state["last_error"] = None
                    state["last_error_kind"] = None
                    state["notified_failure"] = None
                    if state.get("phase") == "failed":
                        state["phase"] = "ready" if state.get("pending") else "installed"
                atomic_json(self.state_path, state)
            self.write_cli_shims()
            return

        package = self.root / "dist/Zed Native Math (Community)"
        if (package / "Zed.exe").is_file() and (package / "native-math-manifest.json").is_file():
            manifest = read_json(package / "native-math-manifest.json")
            zed_info = manifest.get("zed") or {}
            version(zed_info.get("tag"))
            commit = zed_info.get("commit")
            if not isinstance(commit, str) or len(commit) < 7:
                raise UpdateError("Initial package manifest is missing the Zed commit")
            current = {
                "tag": zed_info["tag"],
                "zed_tag": zed_info["tag"],
                "commit": commit,
                "directory": str(package),
                "manifest": str(package / "native-math-manifest.json"),
            }
        else:
            release_info = latest_release(repo)
            current = self.download_candidate(release_info)

        atomic_json(self.state_path, {
            "enabled": True,
            "mode": "release",
            "channel": "stable",
            "repository": repo,
            "check_interval_seconds": INTERVAL_SECONDS,
            "current": current,
            "previous": None,
            "pending": None,
            "phase": "installed",
            "last_checked": now(),
        })
        self.write_cli_shims()

    def command(self, command, cwd=None, environment=None):
        return run(command, cwd, environment, self.log)

    def validate_package(self, directory, recipe):
        missing = [item for item in REQUIRED_PACKAGE_FILES if not (directory / item).is_file()]
        if missing:
            raise UpdateError("Downloaded package is missing required files: " + ", ".join(missing))
        output = self.command([directory / "Zed.exe", "--help"])
        if "PATHS_OR_URLS" not in output:
            raise UpdateError("Packaged GUI command entry did not start correctly")
        output = self.command([directory / "bin/zed.exe", "--version"])
        expected_version = recipe["zed"]["tag"].removeprefix("v")
        if ("Zed " + expected_version + " " not in output
                or recipe["zed"]["commit"] not in output
                or str(directory).lower() not in output.lower()):
            raise UpdateError("Packaged CLI does not resolve the expected GUI: " + output)

    def download_candidate(self, release_info):
        tag = release_info["tag"]
        url = release_info["download_url"]
        downloads = self.work / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        archive = downloads / f"{tag}-{time.time_ns()}.zip"
        directory = self.root / "dist/windows-versions" / f"{tag}-{time.time_ns()}"
        request = urllib.request.Request(url, headers={
            "User-Agent": "Zed-Native-Math-Windows-Updater",
            "Accept": "application/octet-stream",
        })
        try:
            with self.log.open("a", encoding="utf-8") as log:
                log.write(f"\n[{now()}] Downloading release {tag}: {url}\n")
            with open_url(request, timeout=180) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            extract_zip_safely(archive, directory)
            manifest_path = directory / "native-math-manifest.json"
            if not manifest_path.is_file():
                raise UpdateError("Downloaded release is missing native-math-manifest.json")
            recipe = read_json(manifest_path)
            zed_info = recipe.get("zed") or {}
            expected_zed_tag = base_zed_tag(tag)
            if zed_info.get("tag") != expected_zed_tag:
                raise UpdateError(
                    f"Release {tag} manifest mismatch: expected {expected_zed_tag}, got {zed_info.get('tag')}"
                )
            commit = zed_info.get("commit")
            if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
                raise UpdateError(f"Release {tag} manifest has an invalid Zed commit")
            self.validate_package(directory, recipe)
            return {
                "tag": tag,
                "zed_tag": zed_info["tag"],
                "commit": commit,
                "directory": str(directory),
                "manifest": str(manifest_path),
            }
        except Exception:
            if directory.exists():
                shutil.rmtree(directory, ignore_errors=True)
            raise
        finally:
            if archive.exists():
                archive.unlink()

    def sync(self, force=False):
        with lock(self.work / "update.lock", blocking=False) as acquired:
            if not acquired:
                return "already-running"
            state = self.state()
            if not state["enabled"]:
                return "disabled"
            interval = state.get("check_interval_seconds", INTERVAL_SECONDS)
            checked = state.get("last_checked")
            if not force and checked and time.time() - datetime.fromisoformat(checked).timestamp() < interval:
                return "not-due"
            repo = self.release_repository()
            tag = None
            try:
                release_info = latest_release(repo)
                tag = release_info["tag"]
                self.change(last_checked=now(), latest_tag=tag, last_error=None, last_error_kind=None)
                installed = state.get("pending") or state["current"]
                if version(tag) <= version(installed["tag"]):
                    self.change(phase="ready" if state.get("pending") else "up-to-date")
                    return "up-to-date"
                if state.get("held_tag") == tag:
                    self.change(phase="rolled-back")
                    return "held-after-rollback"
                if state.get("failed_tag") == tag and not force:
                    self.change(phase="failed", last_error=state.get("failure_detail"), last_error_kind="update")
                    return "failed-awaiting-retry"
                self.change(phase="downloading", downloading_tag=tag, failed_tag=None, failure_detail=None)
                candidate = self.download_candidate(release_info)
                self.change(pending=candidate, phase="ready", downloading_tag=None,
                            failed_tag=None, failure_detail=None, last_error=None, ready_at=now())
                self.prune_unused(self.state())
                return "ready"
            except Exception as error:
                retry_network = isinstance(error, (urllib.error.URLError, TimeoutError, ConnectionError))
                checked_at = now()
                detail = str(error)
                kind = "network" if retry_network else "update"
                fields = dict(phase="failed", last_checked=checked_at, last_error=detail,
                              last_error_kind=kind, downloading_tag=None)
                if not retry_network:
                    fields.update(failed_tag=tag, failure_detail=detail)
                self.change(**fields)
                stage = f"downloading {tag}" if tag else f"checking {release_api_url(repo)}"
                with self.log.open("a", encoding="utf-8") as log:
                    log.write(f"\n[{checked_at}] {kind} error while {stage}: {detail}\n")
                return "failed"

    def active_directories(self, state):
        kept = set()
        for slot in ("current", "previous", "pending"):
            entry = state.get(slot)
            if entry and entry.get("directory"):
                kept.add(Path(entry["directory"]).resolve())
        return kept

    def prune_unused(self, state):
        removed = []
        freed_bytes = 0
        kept = self.active_directories(state)
        versions_dir = self.root / "dist/windows-versions"
        if versions_dir.is_dir():
            for child in versions_dir.iterdir():
                if child.is_dir() and child.resolve() not in kept:
                    freed_bytes += directory_size(child)
                    shutil.rmtree(child, ignore_errors=True)
                    removed.append(str(child))
        backups_dir = self.work / "database-backups"
        if backups_dir.is_dir():
            protected_backup = (
                Path(state["last_database_backup"]).resolve()
                if state.get("last_database_backup")
                else None
            )
            snapshots = sorted(
                (p for p in backups_dir.iterdir() if p.is_dir()),
                key=lambda p: (int(p.name) if p.name.isdigit() else -1, p.stat().st_mtime, p.name),
            )
            for stale in snapshots[:-MAX_DATABASE_BACKUPS]:
                if protected_backup and stale.resolve() == protected_backup:
                    continue
                freed_bytes += directory_size(stale)
                shutil.rmtree(stale, ignore_errors=True)
                removed.append(str(stale))
        return {"removed": removed, "freed_bytes": freed_bytes}

    def cleanup_legacy_build_caches(self, dry_run=False):
        """Remove obsolete local source-build caches without touching active releases."""
        state = self.state() if self.state_path.is_file() else {}
        kept = self.active_directories(state)
        targets = [
            self.root / ".work/zed-upstream",
            self.root / ".work/update-sources",
            self.root / ".work/cargo",
            self.root / ".work/rustup",
            self.root / ".work/tools",
            self.root / ".work/smoke-data",
            self.root / ".work/rustup-init.exe",
            self.work / "webrtc",
            self.work / "candidates",
            self.work / "downloads",
        ]
        work_root = self.root / ".work"
        if work_root.is_dir():
            targets.extend(sorted(work_root.glob("smoke-update-*")))
        if self.work.is_dir():
            targets.extend(sorted(self.work.glob("acp-*")))
        legacy_dist = self.root / "dist/Zed Native Math (Community)"
        if legacy_dist.is_dir() and legacy_dist.resolve() not in kept:
            targets.append(legacy_dist)
        versions_dir = self.root / "dist/windows-versions"
        if versions_dir.is_dir():
            for child in sorted(versions_dir.iterdir()):
                if child.is_dir() and child.resolve() not in kept:
                    targets.append(child)

        removed = []
        freed_bytes = 0
        seen = set()
        for target in targets:
            resolved = target.resolve()
            if resolved in seen or resolved in kept or not target.exists():
                continue
            seen.add(resolved)
            size = directory_size(target)
            freed_bytes += size
            removed.append({"path": str(target), "bytes": size})
            if not dry_run:
                if target.is_dir():
                    rmtree_force(target)
                else:
                    try:
                        os.chmod(target, stat.S_IWRITE)
                    except OSError:
                        pass
                    target.unlink(missing_ok=True)
        return {"dry_run": dry_run, "freed_bytes": freed_bytes, "items": removed}

    def promote(self, running):
        with lock(self.work / "state.lock"):
            state = self.state()
            if state["enabled"] and state.get("pending") and not running:
                state["last_database_backup"] = self.backup_database()
                state["previous"] = state["current"]
                state["current"] = state.pop("pending")
                state["pending"] = None
                state["activated_at"] = now()
                if not state.get("downloading_tag"):
                    state["phase"] = "up-to-date"
                atomic_json(self.state_path, state)
                self.prune_unused(state)
                self.refresh_shell_icons(state["current"]["directory"])
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
            self.refresh_shell_icons(state["current"]["directory"])

    def run_cli(self, arguments):
        state = self.promote(stable_is_running())
        current = state["current"]
        cli_binary = Path(current["directory"]) / "bin/zed.exe"
        if not cli_binary.is_file():
            raise UpdateError("Active CLI binary is missing; run install-windows-updates.ps1 or roll back")
        environment = os.environ.copy()
        environment.pop("ZED_STATELESS", None)
        # Preserve caller's working directory so relative paths like `zed .` resolve properly.
        result = subprocess.run([str(cli_binary), *arguments], env=environment)
        return result.returncode

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
            ctypes.windll.user32.MessageBoxW(
                None,
                "云端预编译更新未完成，继续使用现有数学版。\n\n"
                + str(state["last_error"])[-1200:] + "\n\n日志：" + str(self.log),
                "Zed Native Math 更新未完成", 0x30,
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=[
        "initialize", "sync", "launch", "cli", "status", "rollback", "disable", "cleanup",
    ])
    parser.add_argument("--force", action="store_true", help="check now and retry a failed candidate")
    parser.add_argument("--dry-run", action="store_true", help="preview paths without deleting (cleanup)")
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
        elif arguments.action == "cli":
            return updater.run_cli(forwarded)
        elif arguments.action == "status":
            print(json.dumps(updater.state(), ensure_ascii=False, indent=2))
        elif arguments.action == "disable":
            updater.change(enabled=False)
        elif arguments.action == "rollback":
            updater.rollback(stable_is_running())
        elif arguments.action == "cleanup":
            report = updater.cleanup_legacy_build_caches(dry_run=arguments.dry_run)
            print(json.dumps(report, ensure_ascii=False, indent=2))
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
