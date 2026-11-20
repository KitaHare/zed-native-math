# Changelog

## Unreleased

- Build and package Zed for Windows x64. The optional desktop updater follows official stable releases, tests each patched build before switching, and keeps the previous version for rollback.
- Update the Windows patch to Zed 1.29.2 while retaining the 1.25.3 baseline. A repaired patch can build in a new worktree without overwriting an earlier conflict.
- Use stable feature-flag defaults in Windows debug packages. Google Antigravity 1.5.0 can open a new ACP thread without the experimental v2 handshake error.
- Preserve Chinese line wrapping, punctuation, selection and nested list layout around inline formulas.
- Remove custom file digest checks; keep the Zed revision and patch compatibility checks.

## 0.1.0 — Experimental source release

- Port native RaTeX rendering to Zed 1.25.3 Agent chat and Markdown preview on Apple Silicon macOS.
- Support dollar and bracket delimiters, preserve code and source offsets, and keep standalone equals or minus signs inside multiline formulas.
