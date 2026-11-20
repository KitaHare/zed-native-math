# SPDX-License-Identifier: GPL-3.0-or-later
"""Select a platform's first-build recipe from the repository manifest."""


def default_recipe(catalog, platform):
    if platform != "win32":
        return catalog

    update = catalog["windows_update"]
    seed = update["seed"]
    # Keep shared release metadata, but never label the newer package with the
    # old pin's verification results or an untested macOS claim.
    recipe = {k: v for k, v in catalog.items() if k not in ("verification", "windows_update")}
    recipe.update(
        zed={"tag": seed["tag"], "commit": seed["commit"]},
        patch={"file": seed["patch"]},
        tested_platforms=["Windows x64"],
        windows_verification=update["verification"],
        rust_toolchain=seed.get("rust_toolchain", catalog["rust_toolchain"]),
    )
    return recipe
