# Zed Native Math

在 Zed 的 Markdown 预览、内置 Agent 和 ACP 外部 Agent 对话中原生渲染 LaTeX 数学公式。行内公式支持 `$…$`、`\(…\)`，块级公式支持 `$$…$$`、`\[…\]`；支持中文混排自动换行与嵌套列表缩进，代码块内的 LaTeX 保持原文不误解析。

![Zed 原生 Markdown 预览中的矩阵与傅里叶变换](assets/native-rendering.jpg)

这是一个社区维护的 Zed 原生数学补丁与**云端预编译发布仓库**（不是扩展商店插件）。**Windows x64** 与 **macOS Apple Silicon（`arm64`）** 均由 GitHub Actions 自动测试、编译并发布到 [GitHub Releases](https://github.com/KitaHare/zed-native-math/releases)，本机无需安装 Rust、Visual Studio、Xcode 或保留几十 GB 编译缓存。各版本补丁与验证信息见 [manifest.json](manifest.json)。

---

## 快速安装

### Windows x64（一键安装 + 自动更新 + 系统集成）

仅需 **Python 3.9+**（含 `pythonw.exe`，本机 0 编译）。克隆本仓库后在根目录运行：

```powershell
.\install-windows-updates.ps1 install
```

安装完成后会自动配齐：
- **云端拉取成品**：首次运行直接从 [GitHub Releases](https://github.com/KitaHare/zed-native-math/releases) 下载最新 Windows x64 压缩包，并校验 GUI（`--help`）与 CLI（`--version`）；
- **完整系统集成**：自动创建桌面 **Zed** 与开始菜单 **Zed Native Math** 快捷方式、注册资源管理器右键菜单（文件 / 文件夹 / 空白处「通过 Zed 打开」）、注册「打开方式」与 `zed://` 协议（用于登录回调和深链接），并将 `dist\bin` 加入用户 `PATH`（可直接在终端使用 `zed` 命令）；
- **后台静默检查更新**：注册计划任务，在登录、启动和每 **6 小时**自动检查一次新 Release；下载就绪后，**保存文件并完全退出 Zed 再重新启动**，启动器会自动备份 `%LOCALAPPDATA%\Zed\db` 并切换到新版本（自动清理更早旧版，仅保留当前版、上一回退版及最近 3 份数据库快照）。

> 社区版与官方 Zed 共用 `%APPDATA%\Zed` 配置和 `%LOCALAPPDATA%\Zed\db` 对话数据，安装完成后可直接卸载官方版 Zed（切换或启动前请先完全退出正在运行的 Zed 进程）。

常用维护命令：

```powershell
.\install-windows-updates.ps1 status           # 查看当前版本、待切换版本或上次检查状态
.\install-windows-updates.ps1 check            # 立即检查云端 Release
.\install-windows-updates.ps1 retry            # 网络波动或失败后强制重试
.\install-windows-updates.ps1 rollback         # 完全退出 Zed 后退回上一版本
.\install-windows-updates.ps1 disable          # 暂停自动检查更新（恢复用 install）
.\install-windows-updates.ps1 uninstall        # 移除计划任务、壳层集成与 PATH，保留程序与数据
.\install-windows-updates.ps1 cleanup -DryRun  # 预览可回收的旧本地编译缓存
.\install-windows-updates.ps1 cleanup          # 清理旧本地编译缓存（不影响当前版与回退版）
```

> 回退只切回程序二进制，不会覆盖升级后的聊天记录；如需还原数据库，先备份当前数据，再按 `status` 中的 `last_database_backup` 路径恢复。

---

### macOS（Apple Silicon `arm64`，直接下载预编译包）

1. 前往 [GitHub Releases](https://github.com/KitaHare/zed-native-math/releases) 下载最新版的 `zed-native-math-<tag>-macos-arm64.zip`。
2. 解压后将 `Zed Native Math (Community).app` 拖入 `/Applications`。
3. 由于社区版采用本机临时签名（`codesign --sign -`）、未经付费 Apple 开发者账号公证，**首次打开前**在终端执行一次清除隔离属性：

```bash
xattr -dr com.apple.quarantine "/Applications/Zed Native Math (Community).app"
```

4. （可选）如需在终端使用 `zed` 命令，可将包内自带的 `cli` 链接到 `PATH`：

```bash
ln -sf "/Applications/Zed Native Math (Community).app/Contents/MacOS/cli" /usr/local/bin/zed
```

保存文件并退出官方 Zed 后直接启动即可，会自动沿用现有配置与对话数据；需要更新时直接下载新版压缩包覆盖 `.app` 即可。

---

## 云端自动构建机制（GitHub Actions）

所有发布流程定义在 [release.yml](.github/workflows/release.yml)，并由 [ci.yml](.github/workflows/ci.yml) 在每次提交时跨平台运行单元测试：

- **三类触发方式**：
  - **每周自动巡检（`schedule`）**：每周一 02:00 UTC（北京时间周一 10:00）自动检查上游 Zed 最新稳定版；
  - **推送触发（`push`）**：修改 `manifest.json`、`patches/**`、`build.py` 或 `build_recipe.py` 推到 `main` 时自动触发；
  - **手动触发（`workflow_dispatch`）**：可在 GitHub Actions 页面随时手动运行，支持指定 `zed_tag`（留空为基线版本，填 `latest` 追踪上游最新）、`platforms`（`all` / `windows` / `macos`）、`release_suffix` 与 `force_rebuild`。
- **预检 + 双平台并行构建**：
  - **`plan` 阶段（`ubuntu-latest`）**：按平台分别检查对应压缩包是否已发布（已发布的平台直接跳过，不重复编译）；随后做轻量 `git apply --3way` 兼容性预检——若上游改动引起补丁冲突，立刻停止并在仓库自动创建追踪 Issue，不浪费构建时间。
  - **`build-windows`（`windows-2025`）与 `build-macos`（`macos-26`）**：并行运行原生 `markdown` 数学单元测试、编译精简 Release（关闭 LTO 与调试符号以控制内存与体积）、打包校验并发布到同一个 GitHub Release，同时随包附上对应版本的 `.patch` 与 manifest 以满足 GPL-3.0 源码对应要求。
- **轻量化补丁结构**：仅改动 `crates/markdown`（并将解析、排版、渲染与单测拆为独立模块 `katex.rs`、`math_parser.rs`、`math_layout.rs`、`math_tests.rs`），不修改 `acp_thread`、`agent_ui`、`mermaid` 等外部模块，尽量降低上游更新时的合入冲突率。

---

## 本地从源码构建（可选 / 开发者）

如果你需要调试补丁或在本地自行编译（以当前基线 `v1.30.2` 为例）：

### Windows x64

需安装 Git、Python 3.9+、rustup、CMake、Visual Studio 2022 17.14+（Desktop development with C++、MSVC 14.44+、对应版本 Spectre-mitigated libraries、Windows 11 SDK 10.0.26100+）；路径含中文时需安装 [NASM 3.02 Windows x64](https://www.nasm.us/pub/nasm/releasebuilds/3.02/win64/) 并加入 `PATH`。

```powershell
rustup toolchain install 1.98.1-x86_64-pc-windows-msvc --profile minimal
git clone --depth 1 --branch v1.30.2 --single-branch https://github.com/zed-industries/zed.git .work/zed-upstream
python build.py prepare --platform win32 --source .work/zed-upstream --tag v1.30.2 --output-dir .work/prepared
.\build-windows.ps1 test -Source .work/zed-upstream -Manifest .work/prepared/manifest.json
.\build-windows.ps1 build -Source .work/zed-upstream -Manifest .work/prepared/manifest.json -Release
.\build-windows.ps1 package -Source .work/zed-upstream -Manifest .work/prepared/manifest.json -Destination dist/package -Release
```

### macOS Apple Silicon

需安装 Git、Python 3.9+、rustup、CMake、Xcode（含 macOS SDK 26+）以及同版本的官方 `/Applications/Zed.app` 作为打包模板：

```bash
rustup toolchain install 1.98.1-aarch64-apple-darwin --profile minimal
git clone --depth 1 --branch v1.30.2 --single-branch https://github.com/zed-industries/zed.git .work/zed-upstream
python3 build.py prepare --platform win32 --source .work/zed-upstream --tag v1.30.2 --output-dir .work/prepared
python3 build.py test --source .work/zed-upstream --manifest .work/prepared/manifest.json
python3 build.py build --source .work/zed-upstream --manifest .work/prepared/manifest.json --release
python3 build.py package --source .work/zed-upstream --manifest .work/prepared/manifest.json \
  --template /Applications/Zed.app --destination "dist/Zed Native Math (Community).app" --release
```

---

## 验证与开源许可

- **渲染检查**：在 Zed 中打开 [tests/native-math-smoke.md](tests/native-math-smoke.md) 的 Markdown 预览，可一键验证四种分隔符、矩阵、中文换行及列表缩进。
- **脚本回归测试**：在仓库根目录运行 `python -m unittest discover -s tests -v`（macOS 用 `python3`）。
- **来源与许可**：数学渲染基于 [RaTeX](https://github.com/erweixin/RaTeX)，移植自 [loomou 的 Zed PR #57339](https://github.com/zed-industries/zed/pull/57339)。本仓库补丁与构建脚本采用 [GPL-3.0-or-later](LICENSE)，完整第三方归属与再分发说明见 [NOTICE.md](NOTICE.md)。
