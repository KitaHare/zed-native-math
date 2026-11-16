# Changelog

## Unreleased

- Add a Windows stable-release updater and desktop launcher: check every 30 minutes and at logon/launch, merge the math patch, test/build/package separately, activate after exit and retain rollback.
- Add an adapted v1.28.1 update seed that preserves the upstream MarkdownOptions diagram field for new callers.
- Support explicit build manifests without changing the original pinned build defaults; embed the actual release version and commit in Windows builds.
- Remove custom patch digest verification and the release digest inventory; retain version and patch-compatibility checks.
- Add a Windows x64 PowerShell entry point that initializes the MSVC environment and supports project-local Rust tools.
- Build the Windows GUI and CLI without Metal features; package them with the terminal runtime into a separate application directory.
- Disable official binary updates at compile time for Windows community builds.
- Read upstream notices as UTF-8 and run the full Markdown library test suite.
- Add Windows build-helper regression tests and a Markdown visual smoke fixture.
- Verify all 213 native Markdown tests on Windows x64, including formula SVG decoding.
- Complete the Windows debug GUI/CLI build and directory package with MSVC 14.44 and SDK 26100.
- Visually verify the packaged app's Markdown preview and an offline-imported Agent thread, including formulas, Chinese wrapping, nested lists and literal code.
- Prefer project-local official NASM 3.02 for Chinese build paths; check the full application's Windows SDK requirement without blocking Markdown tests.
- Keep nested lists and code blocks below inline math text in tight list items.
- Wrap Chinese text around inline math at Unicode word boundaries as the container resizes.
- Keep opening and closing punctuation with inline formulas.
- Preserve selection, code chips, links and explicit line breaks across inline layout fragments.
- Validate the full Markdown suite: 213 tests pass.

## 0.1.0 — experimental source release

- Adapt native RaTeX rendering to Zed 1.25.3 Markdown and Agent views.
- Support dollar and backslash-parenthesis/bracket math delimiters.
- Preserve source offsets and protect code content.
- Fix standalone equals/minus lines breaking multiline formulas through CommonMark setext headings.
- Add matrix/Fourier regression coverage and a portable, version-pinned source build helper.

Tested locally on Apple Silicon macOS. Other platforms and Zed versions are unverified. No prebuilt app or general-purpose automatic updater is included.
