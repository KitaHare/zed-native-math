#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply, test and build the version-pinned Zed Native Math community patch."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


ROOT = Path(__file__).resolve().parent
MANIFEST = json.loads((ROOT / "manifest.json").read_text())
PATCH = ROOT / MANIFEST["patch"]["file"]
BASE_COMMIT = MANIFEST["zed"]["commit"]
FEATURES = "gpui_platform/runtime_shaders,util/debug-embed"
NOTICE = "> [!IMPORTANT]\n> Remove this line to confirm you've reviewed this PR before submitting.\n"


class BuildError(RuntimeError):
    pass


def run(arguments, source=None, capture=False, environment=None, check=True):
    return subprocess.run([str(argument) for argument in arguments], cwd=source,
                          env=environment, check=check,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None)


def verify_checksum(patch, expected):
    if hashlib.sha256(patch.read_bytes()).hexdigest() != expected:
        raise BuildError("Patch checksum does not match manifest.json")


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
    if not readme.read_text().startswith(NOTICE):
        readme.write_text(NOTICE + "\n" + readme.read_text())
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
    environment["ZED_BUNDLE"] = "true"
    environment["RUSTUP_TOOLCHAIN"] = MANIFEST["rust_toolchain"]
    return environment


def executable_path(source):
    target = Path(os.environ.get("CARGO_TARGET_DIR", "target"))
    if not target.is_absolute():
        target = source / target
    return target / "debug/zed"


def package_application(source, template, destination):
    if destination.exists() or destination.is_symlink():
        raise BuildError("Destination already exists; refusing to overwrite it: " + str(destination))
    if sys.platform != "darwin":
        raise BuildError("Application packaging is supported on macOS only")
    with (template / "Contents/Info.plist").open("rb") as stream:
        information = plistlib.load(stream)
    version = MANIFEST["zed"]["tag"].removeprefix("v")
    if information.get("CFBundleShortVersionString") != version:
        raise BuildError("The official Zed.app template must match " + version)
    binary = executable_path(source)
    if not binary.is_file():
        raise BuildError("Build the native executable first")
    run(["codesign", "--verify", "--deep", "--strict", template])
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, destination, symlinks=True)
    shutil.copy2(binary, destination / "Contents/MacOS/zed")
    information.update(CFBundleName="Zed Native Math (Community)",
                       CFBundleDisplayName="Zed Native Math (Community)",
                       CFBundleIdentifier="local.zed.NativeMath", CFBundleExecutable="zed",
                       ZedMathBaseCommit=BASE_COMMIT,
                       ZedMathPatchSHA256=MANIFEST["patch"]["sha256"])
    environment = information.setdefault("LSEnvironment", {})
    environment["ZED_UPDATE_EXPLANATION"] = (
        "Community source build. Update by rebuilding from the Zed Native Math patch repository."
    )
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
    parser.add_argument("--template", type=Path, default=Path("/Applications/Zed.app"))
    parser.add_argument("--destination", type=Path,
                        default=Path.home() / "Applications/Zed Native Math (Community).app")
    arguments = parser.parse_args()
    source = arguments.source.expanduser().resolve()
    try:
        verify_checksum(PATCH, MANIFEST["patch"]["sha256"])
        verify_repository(source, BASE_COMMIT)
        if arguments.action == "apply":
            apply_patch(source, PATCH, BASE_COMMIT)
        elif arguments.action == "verify":
            if not applied(source, PATCH):
                run(["git", "apply", "--check", PATCH], source)
            print("Patch checksum and version compatibility verified.")
        else:
            if not applied(source, PATCH):
                raise BuildError("Apply the math patch before building or packaging")
            if arguments.action == "package":
                package_application(source, arguments.template.expanduser().resolve(),
                                    arguments.destination.expanduser().absolute())
            else:
                environment = build_environment()
                if arguments.action == "test":
                    command = ["cargo", "test", "--locked", "-p", "markdown", "--lib",
                               "--features", FEATURES, "math", "--", "--test-threads=1"]
                else:
                    command = ["cargo", "build", "--locked", "-p", "zed", "--bin", "zed",
                               "--features", FEATURES]
                run(command, source, environment=environment)
        return 0
    except (OSError, subprocess.CalledProcessError, BuildError) as error:
        print("Build stopped: " + str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
