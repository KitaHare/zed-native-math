# Attribution and license notices

This is an unofficial community source patch for Zed. It is not a Zed Industries release.

- Zed upstream: https://github.com/zed-industries/zed
- The Windows and macOS source revisions are recorded in manifest.json (windows_update.seed and zed, respectively).
- Modified editor crates (markdown, acp_thread, agent_ui and markdown_preview) declare GPL-3.0-or-later. The GPL v3 license text is included in LICENSE.
- Zed's other components retain their upstream terms. The upstream Apache license notice is preserved in licenses/ZED-LICENSE-APACHE; this does not make this patch Apache-licensed.
- Math rendering implementation: loomou, https://github.com/zed-industries/zed/pull/57339, reference commit 0676a0c. The patch includes adapted implementation and tests from that work. Credit must remain with the upstream contributors.
- Additional work: Zed 1.25.3 integration, bracket math delimiters, protection from CommonMark setext heading parsing, and related regression coverage.
- RaTeX dependencies: ratex-parser, ratex-layout, ratex-svg and ratex-types 0.1.9, declared MIT, https://github.com/erweixin/RaTeX. Their source and fonts are fetched through Cargo and are not vendored in this source package.
- Additional build tooling in this package is distributed under GPL-3.0-or-later.

No precompiled Zed application, official icon/font bundle, account configuration, conversation data, or local updater state is distributed here. If distributing a built application separately, include its corresponding source and all applicable third-party notices rather than treating this patch-only archive as a complete binary redistribution package.
