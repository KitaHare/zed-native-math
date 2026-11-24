# Zed Native Math

在 Zed 的 Markdown 预览、内置 Agent 和 ACP 外部 Agent 对话中直接显示 LaTeX 公式。行内支持 `$…$`、`\(…\)`，独立公式支持 `$$…$$`、`\[…\]`。中文会随公式换行，嵌套列表保留缩进，代码里的 LaTeX 保留原文。

![Zed 原生 Markdown 预览中的矩阵与傅里叶变换](assets/native-rendering.jpg)

这是社区维护的**源码补丁**，不是扩展商店插件。首次使用需编译完整 Zed，请为依赖下载和构建缓存预留时间与磁盘空间。各平台的补丁、提交和验证记录见 [manifest.json](manifest.json)。

## Windows x64：直接构建 Zed 1.29.2

这一版已验证数学预览和 Antigravity 1.5.0 新会话。已启用桌面更新器的用户不需要重新执行首次构建。

安装 Git、Python 3.9+、rustup、CMake，以及 Visual Studio 2022 17.14 或对应的 Build Tools。选择 **Desktop development with C++**，包含 MSVC 14.44+ x64/x86 工具、匹配版本的 Spectre-mitigated libraries 和 Windows SDK 10.0.26100+。如果路径含中文，使用 [NASM 3.02 Windows x64](https://www.nasm.us/pub/nasm/releasebuilds/3.02/win64/) 并将其加入 PATH。

在本仓库根目录运行 PowerShell：

```powershell
rustup toolchain install 1.98.1-x86_64-pc-windows-msvc --profile minimal
git clone --depth 1 --branch v1.29.2 --single-branch https://github.com/zed-industries/zed.git .work/zed-upstream
.\build-windows.ps1 apply
.\build-windows.ps1 test
.\build-windows.ps1 build
.\build-windows.ps1 package
```

应用入口是 `dist\Zed Native Math (Community)\Zed.exe`。这是未签名的应用目录，单独复制 EXE 会漏掉运行组件。社区版与官方 stable 共用配置、聊天数据和单实例：**先保存文件并完全退出官方版，再启动社区版。**

### Windows：用桌面图标跟随官方稳定版

首次打包后，运行 `.\install-windows-updates.ps1 install`（需要 Python 附带的 `pythonw.exe`）。它会备份并设置桌面 **Zed** 快捷方式，添加当前用户的计划任务。此后从桌面图标启动；直接运行某个版本的 EXE 不会切换版本。保留仓库、`.work`、`dist` 和安装时使用的 Python 环境。

更新器在登录、启动和每 30 分钟检查一次，合并重复检查。发现新版后，它在本机应用补丁、测试并打包。**成功后，完全退出 Zed，再双击桌面图标**，启动器会先备份数据库再切换。

如果更新失败，当前版仍可用，原因见 `.work/windows-updates/update.log`。HTTPS 断线或超时会记入日志，由后台自动重试，不弹窗。若是补丁冲突，等本仓库提供适配后，在仓库根目录运行 `git pull --ff-only`，再执行下面的 `retry`。更新器跟踪的是上游 Zed，不会替你更新本仓库。

```powershell
.\install-windows-updates.ps1 status     # 查看当前版本、待切换版本或失败原因
.\install-windows-updates.ps1 retry      # 修复问题后立即重试
.\install-windows-updates.ps1 rollback   # 完全退出 Zed 后退回前一版程序
```

回退不会覆盖升级后的聊天；如需恢复数据库，先保留当前数据，再从 `status` 中的 `last_database_backup` 找到升级前快照。要暂停更新，用 `disable`，恢复用 `install`；要移除计划任务并让桌面图标直达当前版，用 `uninstall`。

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

保存文件并退出官方 Zed 后，打开 `~/Applications/Zed Native Math (Community).app`，即可沿用 stable 的配置与聊天。打包不会覆盖已有目标应用；社区版使用本机临时签名，未经 Apple 公证。此仓库的自动更新入口仅适用于 Windows。

## 验证与来源

打开 [公式样例](tests/native-math-smoke.md) 的 Markdown 预览，可以检查四种分隔符、矩阵、中文换行及列表缩进。构建工具与更新器的回归测试使用 `python -m unittest discover -s tests`（macOS 用 `python3`）。

数学渲染基于 RaTeX，移植自 [loomou 的 Zed PR #57339](https://github.com/zed-industries/zed/pull/57339)。补丁和构建工具采用 [GPL-3.0-or-later](LICENSE)；第三方归属与再分发说明见 [NOTICE.md](NOTICE.md)。
