# Zed Native Math — Community Patch

Development source snapshot **0.1.1-dev**. 固定构建基线为 **Zed 1.25.3**；Windows 可启用持续跟踪官方稳定版的更新服务。

在 Zed 原生 Markdown 渲染器中显示 LaTeX 数学公式，覆盖 Agent 聊天与 Markdown 预览。不是网页预览，也不是可从扩展商店直接安装的插件。本项目与 Zed Industries 无隶属关系。

## 功能

- 行内 `$...$` 与 `\(...\)`，块级 `$$...$$` 与 `\[...\]`。
- 原生 RaTeX → SVG → GPUI 渲染，支持主题、字体和异步缓存。
- 覆盖 Zed 自带 Agent，以及通过 ACP 连接的外部 Agent。
- 修复多行公式中独立的 `=` / `-` 行被 CommonMark 当成标题标记，继而损坏矩阵换行、指数和环境名称的问题。
- 行内公式与中文按文字边界换行，括号跟随公式，避免聊天栏缩窄时文字越界。
- 含公式的列表保持上下层级，支持普通列表、编号列表、任务列表及多层嵌套。
- 保留代码块中的 LaTeX 源码，以及数学区域对应的原始字节位置。

以下截图来自原生 Markdown 视图，使用与 Agent 聊天相同的数学渲染组件：

![Native rendering of matrices and Fourier transforms](assets/native-rendering.jpg)

## 验证范围

| 项目 | 状态 |
| --- | --- |
| Zed 基线 | `v1.25.3` / `8dd255c3eafb5231056fe4e6f601cedd011f1c93` |
| 实测平台 | Apple Silicon macOS、Windows x64 |
| macOS 原生数学测试 | 17 项通过，包含 Markdown → RaTeX → SVG → GPUI 图像解码 |
| macOS 完整原生应用构建 | 通过 |
| macOS Agent 聊天显示 | 本地验证通过 |
| macOS 完整 Markdown 测试集 | 213 项通过，包含反复调整宽度、选择与复制 |
| Windows 原生 Markdown 测试 | 213 项通过，包含公式 SVG 解码 |
| Windows 完整应用与打包 | GUI、CLI 的 debug 构建与独立目录打包通过 |
| Windows 更新流程 | 已跟进 `v1.28.1`，217 项原生测试、完整应用构建与打包通过，并实测新版数学预览 |
| Windows Markdown 预览 | 已实测四种分隔符、矩阵、中文换行、嵌套列表、代码保留，以及窄/宽窗口与字体缩放 |
| Windows Agent 聊天显示 | 本地导入测试对话，原生公式与列表显示通过；未调用模型 |
| Linux / Intel Mac / Windows ARM64 | 未验证 |

数学命令支持范围取决于 RaTeX，不提供完整 TeX 文档编译能力。其他 Zed 版本需通过更新流程验证后再切换。

## 从源码构建

需要 Git、Python 3.9+、Rustup 和 CMake。Rust 工具链固定为 `1.98.1`。这是编译完整 Zed，不是给官方应用安装插件；首次构建需要下载大量依赖，并占用较多磁盘和时间。本工具不会自动安装系统依赖。

### Windows x64

安装 Visual Studio 2022 17.14 / Build Tools 的 **Desktop development with C++** 工作负载、**MSVC 14.44 或更新版本**的 x64/x86 工具与对应 Spectre-mitigated libraries，以及 **Windows SDK 10.0.26100 或更新版本**。只需要编译工具时，可以独立安装 Build Tools，不必安装或升级完整 IDE。使用 MSVC，不使用 MinGW Rust 工具链。可参考 [Zed 1.25.3 Windows 构建文档](https://github.com/zed-industries/zed/blob/v1.25.3/docs/src/development/windows.md)的组件清单。

本次完整构建使用 MSVC `14.44.35207`、Windows SDK `10.0.26100.0` 与项目本地 Rust `1.98.1`。

完整应用中的 WebRTC 依赖要求 SDK 定义 `NTDDI_WIN11_GE`，预编译库还需要新版 MSVC 标准库。SDK 22621 会报 `FILE_INFO_BY_HANDLE_CLASS` 未声明；MSVC 14.43 会在最终链接缺少 `__std_find_first_of_trivial_pos_1`。入口只对 `build` 检查这些要求，不阻止已经能在旧环境运行的 Markdown `test`。新 SDK 和工具集可与旧版本并存。

若入口提示缺少 Spectre 库，在 Visual Studio Installer 中选择“修改 → 单个组件”，补装与当前 MSVC 版本匹配的 **x64/x86 Spectre-mitigated libs**，再重跑 `test`。例如 MSVC 14.43 对应 v14.43–17.13 组件；不必为此升级其他工具。下载缓存与已编译对象会继续复用。

项目路径包含中文时，建议使用 [NASM 官方 Windows x64 3.02](https://www.nasm.us/pub/nasm/releasebuilds/3.02/win64/)。本机 WinLibs 附带的 NASM 2.16.03 无法打开含中文的绝对输出路径；官方 3.02 已通过同路径汇编验证。可安装到 PATH，或在项目目录执行以下命令，入口会优先使用项目本地副本：

```powershell
New-Item -ItemType Directory -Force .work/tools | Out-Null
Invoke-WebRequest https://www.nasm.us/pub/nasm/releasebuilds/3.02/win64/nasm-3.02-win64.zip -OutFile .work/tools/nasm-3.02-win64.zip
Expand-Archive .work/tools/nasm-3.02-win64.zip .work/tools/nasm-3.02-win64
```

在本项目目录的 PowerShell 执行：

```powershell
rustup toolchain install 1.98.1-x86_64-pc-windows-msvc --profile minimal
git clone --depth 1 --branch v1.25.3 --single-branch https://github.com/zed-industries/zed.git .work/zed-upstream

.\build-windows.ps1 verify
.\build-windows.ps1 apply
.\build-windows.ps1 test
.\build-windows.ps1 build
.\build-windows.ps1 package
& '.\dist\Zed Native Math (Community)\Zed.exe'
```

`build-windows.ps1` 会在当前进程初始化 Visual Studio x64 编译环境，不修改系统 PATH。已有源码可用 `-Source 路径` 指定；若项目中存在 `.work/cargo/bin/rustup.exe`，会使用 `.work/cargo` 与 `.work/rustup` 中的项目本地工具链。源码与本地工具链目录不会进入 Git。

Windows 构建只启用 `util/debug-embed`，不启用 macOS Metal 特性；同时编译 GUI 和必需的 CLI。`test` 会运行完整 Markdown 库测试，而不再只筛选名字含 `math` 的测试。

打包结果是独立应用目录，包含 `Zed.exe`、`bin/zed.exe`、`conpty.dll`、`OpenConsole.exe` 和许可证文件，不需要官方 Zed 作为模板。缺少终端组件时会报错，已有目标目录不会被覆盖。移动应用时请移动整个目录；双击根目录的 `Zed.exe` 启动，命令行可用 `bin/zed.exe 文件路径`。它不是安装器，不注册右键菜单、文件关联或全局命令。此本地构建没有代码签名，Windows 可能提示未知发布者。

默认构建 debug 版本用于验证。需要优化版时，构建和打包都传 `-Release`；可用 `-Destination 路径` 选择新的打包目录。`CARGO_BUILD_JOBS` 默认 `2`，可按内存余量调整。

启动前保存文件并退出官方 Zed stable：社区版沿用 stable 的配置、聊天数据和单实例标识，官方版未退出时可能把打开请求交给官方版。Windows 社区构建在编译时禁用官方覆盖更新，直接双击也生效。

打开 `tests/native-math-smoke.md` 并切换 Markdown 预览，可检查四种分隔符、矩阵、中文换行、嵌套列表及代码块。本次也将同一内容导入 Agent 测试对话，验证了聊天中的原生渲染。

需要与官方版同时打开以独立测试时，可在单独的 PowerShell 进程设置 `$env:ZED_STATELESS = 'true'`，再用 `--user-data-dir "$PWD/.work/smoke-data"` 启动社区版。该模式隔离配置与数据、使用内存数据库，不会显示或保存正式聊天；日常使用已有聊天时不要设置这些测试参数。

### macOS

额外需要 macOS Command Line Tools。原有入口保持不变。

在本项目目录执行：

```sh
rustup toolchain install 1.98.1 --profile minimal

git clone --depth 1 --branch v1.25.3 --single-branch \
  https://github.com/zed-industries/zed.git ../zed-upstream

python3 build.py verify --source ../zed-upstream
python3 build.py apply --source ../zed-upstream
python3 build.py test --source ../zed-upstream
python3 build.py build --source ../zed-upstream
```

源码版本不受支持、补丁无法应用或已有本地修改时，工具会停止；不会执行 `git reset` 或覆盖已有源码修改。

macOS 构建参数与本地验证一致：`gpui_platform/runtime_shaders,util/debug-embed`，运行时编译 Metal 着色器并嵌入资源。默认最多两个编译任务，可通过 `CARGO_BUILD_JOBS` 调整。工具不依赖作者的 Cargo/Rustup 缓存路径。

#### 打包为独立 macOS 应用

先安装同版本的官方 `/Applications/Zed.app`，作为应用资源与辅助程序模板，再执行：

```sh
python3 build.py package --source ../zed-upstream \
  --template /Applications/Zed.app \
  --destination "$HOME/Applications/Zed Native Math (Community).app"
```

打包使用本机构建的可执行程序和临时签名。已有目标应用时拒绝覆盖。此构建未做 Apple 公证；本项目不提供预编译应用。

运行时使用 Zed stable 通道的数据目录，沿用该通道的本地配置与聊天数据。请保存文件、退出已有 Zed 实例后，再启动社区构建。

### 更新方式

#### Windows：桌面入口自动跟随官方稳定版

先完成上面的首次构建与打包，再在项目目录执行一次：

```powershell
.\install-windows-updates.ps1 install
```

此后照常双击桌面 `Zed`：启动器使用已验证的数学版，并在后台触发更新检查。Windows 计划任务也会在登录时和每 30 分钟运行；30 分钟内的重复检查会合并。任务只在当前用户登录时运行，不保存密码、不要求管理员权限，不弹出编译控制台。

更新器从 Zed 官方 GitHub 的 latest release 获取稳定版本，在独立源码工作树中三方合并数学补丁，保留新版本自己的依赖锁定结果，安装其指定的项目本地 Rust 工具链，运行完整 Markdown 测试、GUI/CLI 构建和打包入口检查。首次跟进采用仓库维护的 `v1.28.1` 适配补丁；后续以当前成功版本的补丁继续合并。原来的固定版本构建入口保持不变。

通过检查的应用保存到独立版本目录，**不会覆盖正在运行的程序，也不会强制退出窗口**。完全退出 stable Zed 后，下次双击桌面入口时先备份 `%LOCALAPPDATA%/Zed/db`，再切换到待更新版，并保留前一版程序。数据库快照位置记录在状态的 `last_database_backup`；回退命令只切换程序，不覆盖升级后的聊天。若新旧版本数据格式不兼容，应保留当前数据库，再从升级前快照恢复。直接点击某个版本目录中的 `Zed.exe` 会绕过这个启动器；要持续跟随更新，请使用桌面入口。

补丁冲突、编译或测试失败时继续使用现版，启动时提示一次错误，细节写入 `.work/windows-updates/update.log`。相同失败版本不会每半小时重复编译；修复后执行 `retry`。网络检查失败会在后续周期重试。更新需要本机编译时间和磁盘，依赖缓存与历史构建会保留以便复用和回退；电脑关机、离线或无人登录时不能跟进。

```powershell
.\install-windows-updates.ps1 status    # 当前版本、官方版本、待切换版本与错误
.\install-windows-updates.ps1 retry     # 立即检查，并重试修复后的失败版本
.\install-windows-updates.ps1 disable   # 停止后台更新；桌面仍可启动现版
.\install-windows-updates.ps1 rollback  # 完全退出 Zed 后退回前一版
.\install-windows-updates.ps1 uninstall # 移除计划任务，桌面改为直达当前数学版
```

回退会暂缓再次安装被回退的那个版本；下一个官方稳定版仍会继续跟进。首次安装前的桌面快捷方式保存在 `.work/desktop-shortcut-backup/`。不要移动或删除项目目录、Python 环境与 `.work` 缓存后继续使用该启动器。

`disable` 会停止后续检查及待更新版切换，正在编译的任务不会被强制中断；再次执行 `install` 可恢复。

定制应用内的官方二进制覆盖更新保持停用，避免丢失数学补丁；跟进更新由这个本地源码更新服务负责。新的上游 API 变更若无法自动合并，仍需要人工适配，不能保证每个官方版本都立即编译成功。

#### macOS

本仓库保留固定版本构建与打包入口。原本使用的 macOS 本机自动编译服务不在此包中；Windows 计划任务不会更改它。

## 本仓库工具测试

```sh
python3 -m unittest discover -s tests -v
```

`manifest.json` 固定基线提交与补丁路径。`verify` 检查 Zed 提交和补丁兼容性，不执行文件摘要校验。`patches/` 中的补丁含本地原生验证时使用的依赖锁文件。

Windows 构建工具 22 项、更新器 24 项测试通过，共 46 项；包括真实 Git 三方合并、失败恢复、版本切换和回退。各版本原生渲染的验证范围见上表。

## 来源与许可证

数学渲染实现移植自 [loomou 的 Zed PR #57339](https://github.com/zed-industries/zed/pull/57339)，参考提交 `0676a0c`。本补丁完成对 Zed 1.25.3 的适配，并增加括号分隔符、CommonMark 标题冲突修复及回归测试。不能把该实现全部标为本项目原创。

修改的 Zed crate 使用 **GPL-3.0-or-later**，此包的源码改动和构建工具按同一许可证提供，见 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)。Zed、GPUI、RaTeX 及字体等上游项目保留各自的许可证和权利；此包不重新授权它们。

## 文件范围

此源码包只包含补丁、可移植构建工具、测试、文档和无个人信息的演示图。未包含任何用户设置、聊天记录、API 密钥、本机更新状态、构建缓存或可执行程序。
