# Zed Native Math

在 Zed 的 Markdown 预览、内置 Agent 和 ACP 外部 Agent 对话中直接显示 LaTeX 公式。行内支持 `$…$`、`\(…\)`，独立公式支持 `$$…$$`、`\[…\]`。中文会随公式换行，嵌套列表保留缩进，代码里的 LaTeX 保留原文。

![Zed 原生 Markdown 预览中的矩阵与傅里叶变换](assets/native-rendering.jpg)

这是社区维护的 Zed 原生数学补丁与云端预编译发布仓库，不是扩展商店插件。Windows 端通过 GitHub Actions 云端编译发布精简 Release 成品，本机无需安装 Rust / Visual Studio / NASM 或保留数十 GB 编译缓存。各平台的补丁、提交和验证记录见 [manifest.json](manifest.json)。

## Windows x64：轻量安装与自动更新（推荐，本机 0 编译）

仅需 Python 3.9+（含 `pythonw.exe`）。在仓库根目录运行：

```powershell
.\install-windows-updates.ps1 install
```

- 如果本地尚无打包好的程序，安装器会直接从本仓库的 **GitHub Releases** 下载最新验证过的 Windows x64 压缩包并校验（GUI `--help` 与 CLI `--version`）。
- 如果你此前已经在旧版本地编译模式下使用过本工具，重新运行 `install` 会无缝保留现有 `current` / `previous` 版本，并切换为云端预编译更新模式。
- 启动器会设置桌面 **Zed** 快捷方式并添加计划任务：登录、启动和每 **6 小时**检查一次新 Release。下载完成后，**保存文件并完全退出 Zed，再双击桌面图标**，启动器会先备份 `%LOCALAPPDATA%\Zed\db` 再切换，并自动清理更早的冗余版本（仅保留当前版与上一回退版，以及最近 3 份数据库快照）。
- 社区版与官方 stable 共用配置、聊天数据和单实例：**切换或启动前请先完全退出官方版 Zed。**

```powershell
.\install-windows-updates.ps1 status           # 查看当前版本、待切换版本或失败原因
.\install-windows-updates.ps1 check            # 立即检查云端 Release
.\install-windows-updates.ps1 retry            # 修复后强制重试
.\install-windows-updates.ps1 rollback         # 完全退出 Zed 后退回前一版程序
.\install-windows-updates.ps1 cleanup -DryRun  # 预览可回收的旧本机编译缓存（不删除当前/回退版）
.\install-windows-updates.ps1 cleanup          # 正式清理旧本机编译缓存（.work/zed-upstream、cargo、rustup 等）
```

回退不会覆盖升级后的聊天；如需恢复数据库，先保留当前数据，再从 `status` 中的 `last_database_backup` 找到升级前快照。要暂停更新用 `disable`，恢复用 `install`；要移除计划任务并让桌面图标直达当前版，用 `uninstall`。

## 云端自动构建与上游冲突检测（GitHub Actions）

- [release.yml](.github/workflows/release.yml) 负责在云端完成上游检查、补丁合入、原生 `markdown` 单测验证、精简 Release 编译（关闭 LTO 与调试符号以控制内存与体积）及 GitHub Release 发布（同时附上对应版本的 `.patch` 与 `native-math-manifest.json`，满足 GPL-3.0 源码对应要求）。
- 触发方式：
  - 推送 `manifest.json` / `patches/**` 到 `main` 时自动为固定基线构建发布；
  - 每周一 02:00 UTC 定时检查上游最新稳定版——先在廉价的 `ubuntu-latest` 上做毫秒级 `git apply --3way` 预检，**如果冲突则立即停止并自动提 Issue，不消耗 Windows 构建时长**；
  - 随时可在 GitHub Actions 页面手动触发 `workflow_dispatch`（可指定 `zed_tag` 或 `latest`）。
- 补丁结构已大幅瘦身：不改动 `acp_thread`、`agent_ui`、`mermaid`、`markdown_preview` 等外部文件，并将公式解析、内联排版与全部数学单测抽离到 `crates/markdown/src/` 下的独立模块文件（`katex.rs`、`math_parser.rs`、`math_layout.rs`、`math_tests.rs`），大幅降低上游重构导致的冲突概率。

## 本地从源码构建（可选 / 开发者）

如需在本机调试补丁或自行编译 Windows Release：

安装 Git、Python 3.9+、rustup、CMake、Visual Studio 2022 17.14+（Desktop development with C++、MSVC 14.44+ x64/x86、匹配版本 Spectre-mitigated libraries、Windows SDK 10.0.26100+）。路径含中文时安装 [NASM 3.02 Windows x64](https://www.nasm.us/pub/nasm/releasebuilds/3.02/win64/) 并加入 PATH。

```powershell
rustup toolchain install 1.98.1-x86_64-pc-windows-msvc --profile minimal
git clone --depth 1 --branch v1.30.2 --single-branch https://github.com/zed-industries/zed.git .work/zed-upstream
.\build-windows.ps1 apply
.\build-windows.ps1 test
.\build-windows.ps1 build -Release
.\build-windows.ps1 package -Release
```

## macOS（Apple Silicon）：构建并打包 Zed 1.25.3

安装 Git、Python 3.9+、rustup、CMake、Xcode 和 Command Line Tools。打包需要同版本的官方 `/Applications/Zed.app`（**1.25.3**）作为模板。在本仓库根目录运行：

```sh
rustup toolchain install 1.98.1 --profile minimal
git clone --depth 1 --branch v1.25.3 --single-branch https://github.com/zed-industries/zed.git ../zed-upstream
python3 build.py apply --source ../zed-upstream
python3 build.py test --source ../zed-upstream
python3 build.py build --source ../zed-upstream
python3 build.py package --source ../zed-upstream
```

保存文件并退出官方 Zed 后，打开 `~/Applications/Zed Native Math (Community).app`，即可沿用 stable 的配置与聊天。打包不会覆盖已有目标应用；社区版使用本机临时签名，未经 Apple 公证。

## 验证与来源

打开 [公式样例](tests/native-math-smoke.md) 的 Markdown 预览，可以检查四种分隔符、矩阵、中文换行及列表缩进。构建工具与更新器的回归测试使用 `python -m unittest discover -s tests`（macOS 用 `python3`）。

数学渲染基于 RaTeX，移植自 [loomou 的 Zed PR #57339](https://github.com/zed-industries/zed/pull/57339)。补丁和构建工具采用 [GPL-3.0-or-later](LICENSE)；第三方归属与再分发说明见 [NOTICE.md](NOTICE.md)。
