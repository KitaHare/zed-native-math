#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve target Zed tag, skip already-published releases, and preflight patch compatibility."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
UPSTREAM_REPO = "https://github.com/zed-industries/zed.git"
UPSTREAM_RELEASE_API = "https://api.github.com/repos/zed-industries/zed/releases/latest"


def fetch_json(url, token=None):
    headers = {
        "User-Agent": "Zed-Native-Math-CI",
        "Accept": "application/vnd.github+json",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def resolve_zed_tag(requested_tag, event_name, manifest):
    seed_tag = manifest["windows_update"]["seed"]["tag"]
    requested = (requested_tag or "").strip()
    if requested and requested.lower() != "latest":
        tag = requested
    elif event_name == "schedule" or requested.lower() == "latest":
        data = fetch_json(UPSTREAM_RELEASE_API, os.environ.get("GITHUB_TOKEN"))
        if data.get("draft") or data.get("prerelease"):
            raise RuntimeError("Upstream latest release endpoint did not return a stable release")
        tag = data["tag_name"]
    else:
        tag = seed_tag
    if not re.fullmatch(r"v\d+\.\d+\.\d+", tag):
        raise RuntimeError(f"Unsupported upstream Zed tag: {tag}")
    return tag


def release_asset_exists(repo, release_tag, asset_name, token=None):
    url = f"https://api.github.com/repos/{repo}/releases/tags/{release_tag}"
    try:
        data = fetch_json(url, token)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return False
        raise
    return any(asset.get("name") == asset_name for asset in data.get("assets") or [])


def write_outputs(path, outputs):
    if not path:
        print(json.dumps(outputs, indent=2))
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, value in outputs.items():
            stream.write(f"{key}={value}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME", "workflow_dispatch"))
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", "KitaHare/zed-native-math"))
    parser.add_argument("--zed-tag", default="")
    parser.add_argument("--release-suffix", default="")
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--github-output", default=os.environ.get("GITHUB_OUTPUT", ""))
    parser.add_argument("--conflict-report", type=Path)
    arguments = parser.parse_args()

    manifest = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
    zed_tag = resolve_zed_tag(arguments.zed_tag, arguments.event, manifest)
    suffix = arguments.release_suffix.strip()
    if suffix and not re.fullmatch(r"math\.\d+", suffix):
        raise RuntimeError(f"Invalid release suffix (expected math.N): {suffix}")
    release_tag = f"{zed_tag}-{suffix}" if suffix else zed_tag
    asset_name = f"zed-native-math-{release_tag}-windows-x64.zip"

    token = os.environ.get("GITHUB_TOKEN")
    if not arguments.force_rebuild and release_asset_exists(arguments.repo, release_tag, asset_name, token):
        write_outputs(arguments.github_output, {
            "should_build": "false",
            "reason": "already-published",
            "zed_tag": zed_tag,
            "release_tag": release_tag,
            "asset_name": asset_name,
            "conflict": "false",
        })
        return 0

    with tempfile.TemporaryDirectory(prefix="zed-preflight-") as tmp:
        checkout = Path(tmp) / "zed"
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", zed_tag, "--single-branch", UPSTREAM_REPO, str(checkout)],
            check=True,
        )
        seed_tag = manifest["windows_update"]["seed"]["tag"]
        if zed_tag != seed_tag:
            # Fetch the seed base commit so git apply --3way always has ancestor blobs.
            subprocess.run(
                ["git", "fetch", "--depth", "1", "origin", "tag", seed_tag],
                cwd=checkout,
                check=True,
            )
        check = subprocess.run(
            [sys.executable, str(ROOT / "build.py"), "prepare",
             "--platform", "win32", "--source", str(checkout), "--tag", zed_tag, "--check-only"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if check.returncode != 0:
            if arguments.conflict_report:
                arguments.conflict_report.parent.mkdir(parents=True, exist_ok=True)
                arguments.conflict_report.write_text(check.stdout, encoding="utf-8")
            write_outputs(arguments.github_output, {
                "should_build": "false",
                "reason": "patch-conflict",
                "zed_tag": zed_tag,
                "release_tag": release_tag,
                "asset_name": asset_name,
                "conflict": "true",
            })
            print(check.stdout, file=sys.stderr)
            # On scheduled checks, let the workflow open a conflict tracking issue cleanly.
            return 0 if arguments.event == "schedule" else 1

        info = json.loads(check.stdout.strip().splitlines()[-1])
        write_outputs(arguments.github_output, {
            "should_build": "true",
            "reason": "ready",
            "zed_tag": info["tag"],
            "zed_commit": info["commit"],
            "rust_toolchain": info["rust_toolchain"],
            "exact_seed": str(info["exact_seed"]).lower(),
            "release_tag": release_tag,
            "asset_name": asset_name,
            "conflict": "false",
        })
        return 0


if __name__ == "__main__":
    sys.exit(main())
