#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply, test and build the version-pinned Zed Native Math community patch."""

import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

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
    "Community source build. Update by rebuilding from the Zed Native Math patch repository."
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
    readme = source / "README.md"
    contents = readme.read_text(encoding="utf-8")
    if not contents.startswith(NOTICE):
        with readme.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(NOTICE + "\n" + contents)
    run(["git", "apply", "--index", patch], source)


def build_environment():
    environment = os.environ.copy()
    for name, value in {
        "CARGO_BUILD_JOBS": "2", "CARGO_INCREMENTAL": "0",
        "CARGO_PROFILE_DEV_DEBUG": "0", "CARGO_PROFILE_DEV_BUILD_OVERRIDE_DEBUG": "0",
        "CARGO_NET_GIT_FETCH_WITH_CLI": "true", "CARGO_HTTP_MULTIPLEXING": "false",
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
    parser.add_argument("action", choices=["verify", "apply", "test", "build", "package"])
    parser.add_argument("--source", type=Path, required=True, help="separate official Zed source checkout")
    parser.add_argument("--manifest", type=Path,
                        help="use this exact recipe instead of the platform default from manifest.json")
    parser.add_argument("--template", type=Path, default=Path("/Applications/Zed.app"))
    parser.add_argument("--destination", type=Path,
                        default=(ROOT / "dist/Zed Native Math (Community)" if sys.platform == "win32"
                                 else Path.home() / "Applications/Zed Native Math (Community).app"))
    parser.add_argument("--release", action="store_true",
                        help="build/package the optimized release executable (tests use debug)")
    arguments = parser.parse_args()
    source = arguments.source.expanduser().resolve()
    try:
        if arguments.manifest:
            configure_manifest(arguments.manifest.expanduser().resolve())
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
