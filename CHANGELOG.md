# Changelog

## Unreleased

- Wrap Chinese text around inline math at Unicode word boundaries as the container resizes.
- Keep opening and closing punctuation with inline formulas.
- Preserve selection, code chips, links and explicit line breaks across inline layout fragments.
- Validate the full Markdown suite: 210 tests pass.

## 0.1.0 — experimental source release

- Adapt native RaTeX rendering to Zed 1.25.3 Markdown and Agent views.
- Support dollar and backslash-parenthesis/bracket math delimiters.
- Preserve source offsets and protect code content.
- Fix standalone equals/minus lines breaking multiline formulas through CommonMark setext headings.
- Add matrix/Fourier regression coverage and a portable, version-pinned source build helper.

Tested locally on Apple Silicon macOS. Other platforms and Zed versions are unverified. No prebuilt app or general-purpose automatic updater is included.
