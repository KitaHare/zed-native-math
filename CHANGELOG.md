# Changelog

## Unreleased

- Rebase and slim the Windows native math patch onto Zed `v1.30.2` (`610ea4b8de1c6e887f03563f067278002f41256e`): remove all edits to `acp_thread`, `agent_ui`, `mermaid`, and `markdown_preview`, and extract parser, inline layout, and unit tests into dedicated modules inside `crates/markdown/src/` to minimize upstream merge conflicts.
- Add GitHub Actions CI and automated Windows x64 Release workflow (`.github/workflows/release.yml`) with cheap Linux preflight patch checks, weekly upstream drift detection, and stripped non-LTO Release packaging.
- Replace local-compilation Windows auto-updates with a lightweight GitHub Releases downloader (`windows_update.py`), checking every 6 hours, pruning stale app versions and older DB backups automatically, and adding `.\install-windows-updates.ps1 cleanup` to reclaim legacy local Rust/Zed build caches.
- Build and package Zed for Windows x64 with stable feature-flag defaults, preserving Chinese line wrapping, punctuation, selection and nested list layout around inline formulas.
- Remove custom file digest checks; keep the Zed revision and patch compatibility checks.

## 0.1.0 — Experimental source release

- Port native RaTeX rendering to Zed 1.25.3 Agent chat and Markdown preview on Apple Silicon macOS.
- Support dollar and bracket delimiters, preserve code and source offsets, and keep standalone equals or minus signs inside multiline formulas.
