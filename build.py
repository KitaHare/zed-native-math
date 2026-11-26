#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply, test, build, and package the Zed Native Math community patch."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

from build_recipe import default_recipe


ROOT = Path(__file__).resolve().parent


def configure_manifest(path, *, platform=None):
    """Explicit manifests are literal recipes; only the default is platform-selected."""
    global MANIFEST, MANIFEST_BYTES, PATCH, BASE_COMMIT
    MANIFEST_BYTES = path.read_bytes()
    catalog = json.loads(MANIFEST_BYTES)
    MANIFEST = default_recipe(catalog, platform) if platform else catalog
    if MANIFEST is not catalog:
        MANIFEST_BYTES = (json.dumps(MANIFEST, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    PATCH = (path.parent / MANIFEST["patch"]["file"]).resolve()
    BASE_COMMIT = MANIFEST["zed"]["commit"]


configure_manifest(ROOT / "manifest.json", platform=sys.platform)
MACOS_FEATURES = "gpui_platform/runtime_shaders,util/debug-embed"
UPDATE_EXPLANATION = (
    "Community release build. Update via the Zed Native Math updater or GitHub Releases."
)
NOTICE = "> [!IMPORTANT]\n> Remove this line to confirm you've reviewed this PR before submitting.\n"


class BuildError(RuntimeError):
    pass


def run(arguments, source=None, capture=False, environment=None, check=True):
    return subprocess.run([str(argument) for argument in arguments], cwd=source,
                          env=environment, check=check,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def verify_repository(source, expected_revision):
    revision = run(["git", "rev-parse", "HEAD"], source, capture=True).stdout.decode().strip()
    if revision != expected_revision:
        raise BuildError("Unsupported Zed revision. Use a checkout of " + MANIFEST["zed"]["tag"])


def applied(source, patch):
    return run(["git", "apply", "--reverse", "--check", patch], source,
               capture=True, check=False).returncode == 0


def prepend_readme_notice(source):
    readme = source / "README.md"
    if not readme.is_file():
        return
    contents = readme.read_text(encoding="utf-8")
    if not contents.startswith(NOTICE):
        with readme.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(NOTICE + "\n" + contents)


def apply_patch(source, patch, expected_revision):
    verify_repository(source, expected_revision)
    if applied(source, patch):
        print("Math patch is already applied.")
        return
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"],
                source, capture=True).stdout
    if dirty:
        raise BuildError("Checkout has local changes; use a separate clean checkout. No files were changed.")
    run(["git", "apply", "--check", patch], source)
    prepend_readme_notice(source)
    run(["git", "apply", "--index", patch], source)


def rust_channel(source):
    text = (source / "rust-toolchain.toml").read_text(encoding="utf-8")
    match = re.search(r'^channel\s*=\s*"(\d+\.\d+\.\d+)"', text, re.MULTILINE)
    if not match:
        raise BuildError("Cannot read the official pinned Rust toolchain")
    return match.group(1)


def prepare_upstream(source, tag, output_dir, *, check_only=False):
    """Apply the maintained seed patch to an exact or newer upstream Zed checkout."""
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise BuildError("Unexpected upstream Zed tag: " + str(tag))
    commit = run(["git", "rev-parse", "HEAD"], source, capture=True).stdout.decode().strip()
    channel = rust_channel(source)
    exact_seed = commit == BASE_COMMIT
    if check_only:
        if applied(source, PATCH):
            return {"tag": tag, "commit": commit, "rust_toolchain": channel, "exact_seed": exact_seed}
        if exact_seed:
            run(["git", "apply", "--check", PATCH], source)
        else:
            run(["git", "apply", "--3way", "--exclude=Cargo.lock", PATCH], source)
        return {"tag": tag, "commit": commit, "rust_toolchain": channel, "exact_seed": exact_seed}

    output_dir = Path(output_dir).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not applied(source, PATCH):
        dirty = run(["git", "status", "--porcelain", "--untracked-files=no"],
                    source, capture=True).stdout
        if dirty:
            raise BuildError("Checkout has local changes; use a clean checkout before prepare.")
        if exact_seed:
            run(["git", "apply", "--index", PATCH], source)
        else:
            run(["git", "apply", "--3way", "--index", "--exclude=Cargo.lock", PATCH], source)
            env = os.environ.copy()
            env.setdefault("CARGO_NET_GIT_FETCH_WITH_CLI", "true")
            env["RUSTUP_TOOLCHAIN"] = channel
            run(["cargo", "update", "-p", "markdown"], source, environment=env)
            run(["git", "add", "Cargo.lock"], source)

    exported_patch = output_dir / f"zed-{tag}-native-math.patch"
    diff = run(["git", "diff", "--binary", "--cached"], source, capture=True).stdout
    if not diff:
        diff = run(["git", "diff", "--binary", "HEAD", "--", ":(exclude)README.md"], source, capture=True).stdout
    exported_patch.write_bytes(diff)
    prepend_readme_notice(source)

    recipe = dict(MANIFEST)
    recipe["zed"] = {"tag": tag, "commit": commit}
    recipe["patch"] = {"file": exported_patch.name}
    recipe["rust_toolchain"] = channel
    recipe["tested_platforms"] = ["Windows x64"]
    recipe.pop("verification", None)
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {
        "tag": tag,
        "commit": commit,
        "rust_toolchain": channel,
        "exact_seed": exact_seed,
        "manifest": str(manifest_path),
        "patch": str(exported_patch),
    }


def open_url(url, timeout=90):
    request = urllib.request.Request(url, headers={"User-Agent": "Zed-Native-Math-Builder"})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            time.sleep(3 * (attempt + 1))


def prepare_webrtc(source, cache_root):
    """Pre-download the exact official WebRTC archive with system Python TLS."""
    environment = build_environment()
    result = run(["cargo", "metadata", "--locked", "--format-version", "1",
                  "--filter-platform", "x86_64-pc-windows-msvc"],
                 source, capture=True, environment=environment)
    metadata = json.loads(result.stdout.decode("utf-8"))
    package = next((item for item in metadata["packages"] if item["name"] == "webrtc-sys-build"), None)
    if package is None:
        return None
    helper = Path(package["manifest_path"]).parent / "src/lib.rs"
    match = re.search(r'pub const WEBRTC_TAG: &str = "(webrtc-[a-f0-9]+-\d+)"',
                      helper.read_text(encoding="utf-8"))
    if not match:
        raise BuildError("The official WebRTC build helper changed; review its download contract")
    tag = match.group(1)
    cache = Path(cache_root).expanduser().resolve() / tag
    directory = cache / "win-x64-release"
    ready = cache / "ready.json"
    if not (ready.is_file() and (directory / "lib/webrtc.lib").is_file()
            and (directory / "include/api/peer_connection_interface.h").is_file()):
        cache.mkdir(parents=True, exist_ok=True)
        archive = cache / "webrtc-win-x64-release.zip"
        url = ("https://github.com/zed-industries/livekit-rust-sdks/releases/download/"
               + tag + "/webrtc-win-x64-release.zip")
        try:
            with open_url(url, timeout=120) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
            with zipfile.ZipFile(archive) as bundle:
                for member in bundle.infolist():
                    if not (cache / member.filename).resolve().is_relative_to(cache.resolve()):
                        raise BuildError("WebRTC archive contains an invalid extraction path")
                bundle.extractall(cache)
        finally:
            if archive.exists():
                archive.unlink()
        if not (directory / "lib/webrtc.lib").is_file():
            raise BuildError("The official WebRTC archive layout changed")
        ready.write_text(json.dumps({"tag": tag, "source": url}, indent=2) + "\n", encoding="utf-8")
    return directory


def build_environment():
    environment = os.environ.copy()
    for name, value in {
        "CARGO_BUILD_JOBS": "2",
        "CARGO_INCREMENTAL": "0",
        "CARGO_PROFILE_DEV_DEBUG": "0",
        "CARGO_PROFILE_DEV_BUILD_OVERRIDE_DEBUG": "0",
        # Keep release builds memory-safe on 16 GB CI runners and strip debug bloat.
        "CARGO_PROFILE_RELEASE_DEBUG": "0",
        "CARGO_PROFILE_RELEASE_LTO": "false",
        "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "16",
        "CARGO_PROFILE_RELEASE_STRIP": "symbols",
        "CARGO_NET_GIT_FETCH_WITH_CLI": "true",
        "CARGO_HTTP_MULTIPLEXING": "false",
    }.items():
        environment.setdefault(name, value)
    environment["RELEASE_CHANNEL"] = "stable"
    environment["RELEASE_VERSION"] = MANIFEST["zed"]["tag"].removeprefix("v")
    environment["ZED_COMMIT_SHA"] = BASE_COMMIT
    environment["ZED_BUNDLE"] = "true"
    environment["RUSTUP_TOOLCHAIN"] = MANIFEST["rust_toolchain"]
    if sys.platform == "win32":
        environment["ZED_UPDATE_EXPLANATION"] = UPDATE_EXPLANATION
    return environment


def build_features():
    return "util/debug-embed" if sys.platform == "win32" else MACOS_FEATURES


def cargo_command(action, release=False):
    if action == "test":
        return ["cargo", "test", "--locked", "-p", "markdown", "--lib",
                "--features", build_features(), "--", "--test-threads=1"]
    command = ["cargo", "build", "--locked", "-p", "zed", "--bin", "zed"]
    if sys.platform == "win32":
        command.extend(["-p", "cli", "--bin", "cli"])
        if not release:
            # Zed treats debug builds as staff in this crate, implicitly enabling ACP beta.
            # Match stable's feature selection without disabling legitimate server-granted flags.
            command.extend(["--config", "profile.dev.package.feature_flags.debug-assertions=false"])
    command.extend(["--features", build_features()])
    if release:
        command.append("--release")
    return command


def executable_path(source, release=False):
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    if not target.is_absolute():
        target = source / target
    if os.environ.get("CARGO_BUILD_TARGET"):
        target /= os.environ["CARGO_BUILD_TARGET"]
    profile = "release" if release else "debug"
    return target / profile / ("zed.exe" if sys.platform == "win32" else "zed")


def package_windows_application(source, destination, release=False):
    if destination.exists() or destination.is_symlink():
        raise BuildError("Destination already exists; refusing to overwrite it: " + str(destination))
    if sys.platform != "win32":
        raise BuildError("Windows packaging must run on Windows")
    binary = executable_path(source, release)
    files = {
        binary: Path("Zed.exe"),
        binary.parent / "cli.exe": Path("bin/zed.exe"),
        binary.parent / "conpty.dll": Path("conpty.dll"),
        binary.parent / "OpenConsole.exe": Path("OpenConsole.exe"),
        ROOT / "LICENSE": Path("LICENSE"),
        ROOT / "NOTICE.md": Path("NOTICE.md"),
        source / "LICENSE-GPL": Path("licenses/ZED-LICENSE-GPL"),
        source / "LICENSE-APACHE": Path("licenses/ZED-LICENSE-APACHE"),
    }
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise BuildError("Windows package inputs are missing; run build first. "
                         "Zed's build must also download ConPTY successfully:\n" + "\n".join(missing))
    destination.mkdir(parents=True)
    for path, relative in files.items():
        output = destination / relative
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, output)
    (destination / "native-math-manifest.json").write_bytes(MANIFEST_BYTES)
    print("Built community application: " + str(destination / "Zed.exe"))


def archive_windows_package(directory, archive_path):
    directory = Path(directory).expanduser().resolve()
    archive_path = Path(archive_path).expanduser().resolve()
    required = [
        "Zed.exe", "bin/zed.exe", "conpty.dll", "OpenConsole.exe",
        "LICENSE", "NOTICE.md", "licenses/ZED-LICENSE-GPL", "licenses/ZED-LICENSE-APACHE",
        "native-math-manifest.json",
    ]
    missing = [item for item in required if not (directory / item).is_file()]
    if missing:
        raise BuildError("Package directory is incomplete:\n" + "\n".join(missing))
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = archive_path.with_name(archive_path.name + ".tmp-" + str(os.getpid()))
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
            for item in sorted(directory.rglob("*")):
                if item.is_file():
                    bundle.write(item, item.relative_to(directory).as_posix())
        os.replace(temporary, archive_path)
    finally:
        if temporary.exists():
            temporary.unlink()
    print("Archived Windows package: " + str(archive_path))


def validate_windows_package(directory, expected_tag, expected_commit):
    directory = Path(directory).expanduser().resolve()
    help_output = run([directory / "Zed.exe", "--help"], capture=True).stdout.decode("utf-8", errors="replace")
    if "PATHS_OR_URLS" not in help_output:
        raise BuildError("Packaged GUI command entry did not start correctly")
    version_output = run([directory / "bin/zed.exe", "--version"], capture=True).stdout.decode("utf-8", errors="replace")
    if ("Zed " + expected_tag.removeprefix("v") + " " not in version_output
            or expected_commit not in version_output
            or str(directory).lower() not in version_output.lower()):
        raise BuildError("Packaged CLI does not resolve the expected GUI: " + version_output.strip())
    print("Validated packaged GUI and CLI: " + version_output.strip())


def package_application(source, template, destination, release=False):
    if destination.exists() or destination.is_symlink():
        raise BuildError("Destination already exists; refusing to overwrite it: " + str(destination))
    if sys.platform != "darwin":
        raise BuildError("Application packaging is supported on macOS only")
    with (template / "Contents/Info.plist").open("rb") as stream:
        information = plistlib.load(stream)
    version = MANIFEST["zed"]["tag"].removeprefix("v")
    if information.get("CFBundleShortVersionString") != version:
        raise BuildError("The official Zed.app template must match " + version)
    binary = executable_path(source, release)
    if not binary.is_file():
        raise BuildError("Build the native executable first")
    run(["codesign", "--verify", "--deep", "--strict", template])
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, destination, symlinks=True)
    shutil.copy2(binary, destination / "Contents/MacOS/zed")
    information.update(CFBundleName="Zed Native Math (Community)",
                       CFBundleDisplayName="Zed Native Math (Community)",
                       CFBundleIdentifier="local.zed.NativeMath", CFBundleExecutable="zed",
                       ZedMathBaseCommit=BASE_COMMIT)
    environment = information.setdefault("LSEnvironment", {})
    environment["ZED_UPDATE_EXPLANATION"] = UPDATE_EXPLANATION
    environment.pop("ZED_STATELESS", None)
    with (destination / "Contents/Info.plist").open("wb") as stream:
        plistlib.dump(information, stream)
    run(["codesign", "--force", "--deep", "--sign", "-", destination])
    run(["codesign", "--verify", "--deep", "--strict", destination])
    print("Built community application: " + str(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=[
        "verify", "apply", "test", "build", "package",
        "prepare", "prepare-webrtc", "archive", "validate-package",
    ])
    parser.add_argument("--source", type=Path, help="separate official Zed source checkout")
    parser.add_argument("--manifest", type=Path,
                        help="use this exact recipe instead of the platform default from manifest.json")
    parser.add_argument("--template", type=Path, default=Path("/Applications/Zed.app"))
    parser.add_argument("--destination", type=Path,
                        default=(ROOT / "dist/Zed Native Math (Community)" if sys.platform == "win32"
                                 else Path.home() / "Applications/Zed Native Math (Community).app"))
    parser.add_argument("--release", action="store_true",
                        help="build/package the optimized release executable (tests use debug)")
    parser.add_argument("--tag", help="upstream Zed tag for prepare (e.g. v1.30.2)")
    parser.add_argument("--output-dir", type=Path, help="output directory for prepare or cache root for prepare-webrtc")
    parser.add_argument("--check-only", action="store_true", help="only check patch compatibility during prepare")
    parser.add_argument("--archive-path", type=Path, help="destination .zip file for archive")
    parser.add_argument("--platform", help="override default manifest recipe platform (e.g. win32)")
    arguments = parser.parse_args()
    try:
        if arguments.manifest:
            configure_manifest(arguments.manifest.expanduser().resolve())
        elif arguments.platform:
            configure_manifest(ROOT / "manifest.json", platform=arguments.platform)
        if arguments.action == "archive":
            if not arguments.archive_path:
                raise BuildError("--archive-path is required for archive")
            archive_windows_package(arguments.destination, arguments.archive_path)
            return 0
        if arguments.action == "validate-package":
            validate_windows_package(arguments.destination, MANIFEST["zed"]["tag"], BASE_COMMIT)
            return 0
        if not arguments.source:
            raise BuildError("--source is required for " + arguments.action)
        source = arguments.source.expanduser().resolve()
        if arguments.action == "prepare":
            tag = arguments.tag or MANIFEST["zed"]["tag"]
            result = prepare_upstream(source, tag, arguments.output_dir or (ROOT / ".work/prepared"),
                                      check_only=arguments.check_only)
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if arguments.action == "prepare-webrtc":
            directory = prepare_webrtc(source, arguments.output_dir or (ROOT / ".work/webrtc"))
            if directory:
                print(str(directory))
            return 0
        verify_repository(source, BASE_COMMIT)
        if arguments.action == "apply":
            apply_patch(source, PATCH, BASE_COMMIT)
        elif arguments.action == "verify":
            if not applied(source, PATCH):
                run(["git", "apply", "--check", PATCH], source)
            print("Zed revision and patch compatibility verified.")
        else:
            if not applied(source, PATCH):
                raise BuildError("Apply the math patch before building or packaging")
            if arguments.action == "package":
                destination = arguments.destination.expanduser().absolute()
                if sys.platform == "win32":
                    package_windows_application(source, destination, arguments.release)
                else:
                    package_application(source, arguments.template.expanduser().resolve(),
                                        destination, arguments.release)
            else:
                environment = build_environment()
                run(cargo_command(arguments.action, arguments.release), source, environment=environment)
        return 0
    except (OSError, subprocess.CalledProcessError, BuildError) as error:
        print("Build stopped: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
