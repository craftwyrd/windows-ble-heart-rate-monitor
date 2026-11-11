# PulseDesk

**简体中文** | [English](README.en.md)

Windows 桌面实时心率悬浮窗。通过 Bluetooth Low Energy（BLE）连接支持标准心率服务的手表或心率带，在桌面显示当前心率和近期变化，无需登录 Garmin Connect。

支持范围以标准 BLE 心率服务及可订阅的通知特征为准，不限制品牌或设备类型；实际连接还取决于设备广播设置、电脑的蓝牙适配器和驱动。

## 功能概览

- 实时显示心率（bpm），提供最近 60 秒曲线、均值和最高值。
- 四种视图：红心胶囊、带迷你曲线的胶囊、透明 HUD、完整曲线与连接面板。
- 无边框窗口，支持拖动、置顶和透明度切换（100% / 88% / 76%）。
- 扫描时优先排列并标记广播标准心率服务 `0x180D` 的设备。
- 信号中断时保留最后心率值，并以文字、图形及琥珀色提示数据已过期。
- 支持系统托盘操作，隐藏窗口后继续接收心率。
- 提供 `--demo` 演示模式，无需心率设备即可预览界面和状态。

## 快速开始

### 运行条件

- Windows 电脑，配备支持 BLE 的蓝牙适配器及可用驱动。
- 支持标准 BLE Heart Rate Service 的设备，并已开启心率广播。
- 运行打包后的 EXE 无需安装 Python；从源码运行或构建则需安装 Python（建议 3.10 或更新版本，包含 Tkinter 和 pip）。

### 连接设备

可从 [GitHub Releases](https://github.com/craftwyrd/windows-ble-heart-rate-monitor/releases/latest) 下载 Windows x64 版 `PulseDesk.exe`；同页的 `SHA256SUMS.txt` 用于核对文件校验值。

1. 开启心率设备的 BLE 广播或连接模式。例如，在 Forerunner 265 上开启“广播心率”，并保持广播开启。
2. 打开电脑蓝牙，运行 `PulseDesk.exe`；本地构建的文件位于 `dist\PulseDesk.exe`。如果只有源码，请先按下方说明运行或构建。
3. 点击 **扫描设备**，选择手表或心率带。优先尝试标有 `[心率 0x180D]` 的设备；无名称设备会显示地址后缀，未标记的设备也可手动尝试连接。
4. 点击 **连接所选设备**。连接成功后自动收为紧凑视图，首次默认为红心胶囊；收到通知后显示实时心率。
5. 双击胶囊展开完整面板，查看最近 60 秒曲线或管理连接。

## 视图与操作

| 视图 | 尺寸 | 显示内容 |
| --- | --- | --- |
| 极简红心胶囊 | 138 × 50 | 红心与当前心率 |
| 极简带曲线 · 方案 C | 196 × 48 | 红心、心率与最近 20 笔心率尾迹 |
| 透明 HUD | 138 × 50 | 去除背景，仅保留红心和带阴影的数字 |
| 完整面板 | 316 × 336 / 316 × 176 | 当前心率、60 秒曲线、状态及可收起的连接区 |

以上为常规模式的布局尺寸；带曲线胶囊使用最近 **20 笔数据**，不等同于固定 20 秒。

- **移动窗口**：拖动胶囊或完整面板顶部拖动区。
- **切换视图**：右键悬浮窗，或使用托盘菜单。胶囊悬停工具栏也可展开面板、切换 HUD 或迷你曲线。
- **悬停工具栏**：通常浮在胶囊上方，靠近屏幕顶边时改在下方显示，心率数字位置保持不变。
- **置顶与透明度**：使用面板按钮、悬停工具栏或窗口右键菜单；默认置顶。
- **托盘操作**：通知区域的红心图标可能藏在 `^` 中。双击显示窗口，右键可显示/隐藏、切换四种视图、切换置顶、断开或退出。
- **隐藏与退出**：托盘“隐藏悬浮窗”仅隐藏界面，仍继续接收数据。窗口关闭按钮或“退出 PulseDesk”会断开连接并结束程序。

## 无设备演示

从源码运行（需先完成下方依赖安装）：

```powershell
.\.venv\Scripts\python.exe app.py --demo
```

或使用本地构建的 EXE：

```powershell
.\dist\PulseDesk.exe --demo
```

演示模式启动时显示模拟心率胶囊。双击展开面板，在连接区的“状态预览”中查看未连接、扫描、有设备、连接中、两位/三位心率、信号中断和连接失败。模拟数据和状态预览按钮仅在此模式启用。

## 开发与构建

以下命令均在项目根目录的 Windows PowerShell 中执行。

### 从源码运行

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

程序使用 Python + Tkinter 构建界面，Bleak 处理 BLE 通信，pystray 和 Pillow 提供托盘图标支持。当前应用界面为中文；英文 README 提供英文使用说明。

### 运行测试

```powershell
.\.venv\Scripts\python.exe -m pytest
```

测试覆盖心率解析、异常通知处理、设备名称显示及 Windows 界面回归，不需要真实蓝牙设备。界面测试需要 Windows 桌面环境，在其他平台自动跳过；自动化测试不能代替真实设备的扫描、连接和通知验证。

### 打包单文件 EXE

先退出正在运行的 PulseDesk，再执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build.ps1
```

脚本自动创建或复用 `.venv`、安装依赖、运行测试，并按 `PulseDesk.spec` 使用 PyInstaller 打包。测试失败时不会继续打包。输出为 `dist\PulseDesk.exe`，包含窗口与 EXE 图标资源，目标电脑无需安装 Python。

### 持续集成与发布

[Windows build and release](https://github.com/craftwyrd/windows-ble-heart-rate-monitor/actions/workflows/build.yml) 在推送 `main`、向 `main` 提交 PR 或手动触发时，使用 Windows x64 / Python 3.10.11 执行同一份 `build.ps1`，运行测试并打包 EXE，再检查演示进程能否持续运行。成功后可在工作流运行页面下载 `PulseDesk-windows-x64` 构建产物（保留 14 天）。

维护者推送 `v主版本.次版本.修订版本` 标签时，流水线会在验证成功后自动发布 GitHub Release，附带 `PulseDesk.exe` 和 `SHA256SUMS.txt`。发布说明优先读取 `docs/releases/<标签>.md`，没有对应文件时自动生成；已发布版本的附件不会被重跑覆盖。首个基础版本为 `v0.1.0`。

### 项目结构

| 路径 | 用途 |
| --- | --- |
| `app.py` | 应用入口、BLE 扫描/连接、通知解析和桌面界面 |
| `test_app.py` | 心率解析与设备显示测试 |
| `test_ui.py` | Windows 界面回归测试 |
| `requirements.txt` | 运行、测试与打包依赖 |
| `build.ps1` / `PulseDesk.spec` | Windows 构建脚本与 PyInstaller 配置 |
| `.github/workflows/build.yml` | Windows 持续集成和标签发布流水线 |
| `docs/releases/` | 各版本的发布说明 |
| `assets/` | 图标资源 |
| `garmin_hr_overlay.py` / `template/` | 界面原型与设计参考 |
| [DESIGN_BRIEF.md](DESIGN_BRIEF.md) | 产品目标与设计需求 |
| `build/` / `dist/` | 构建中间文件与打包产物，不作为源码修改 |

## 常见问题

| 问题 | 建议处理 |
| --- | --- |
| 扫描不到设备 | 确认电脑蓝牙开启、适配器支持 BLE、设备仍在广播；将设备靠近电脑，检查 Windows 蓝牙驱动及相关扫描隐私设置后重新扫描。 |
| 找不到设备名称或心率标记 | 查看未知设备的地址后缀；`[心率 0x180D]` 来自广播信息，缺少标记不一定表示无法连接，可手动选择尝试。 |
| 连接超时或提示缺少 `0x2A37` | 确认设备提供标准 BLE 心率通知，保持设备处于广播或连接模式；若被其他接收端占用，断开其他接收端后重试。 |
| 显示“信号中断” | 蓝牙已断开或约 5 秒以上未收到新数据时，会保留最后值。先确认广播与距离，再在完整面板中重新连接；保留的数值不代表当前实时心率。 |
| 隐藏后找不到窗口 | 在通知区域（含 `^` 隐藏图标）中双击红心图标，或右键选择“显示悬浮窗”。 |
| 构建提示先关闭 PulseDesk | 从托盘菜单退出程序；仅隐藏窗口不会结束进程。 |

## 兼容性与范围

PulseDesk 订阅标准 BLE Heart Rate Service（`0x180D`）中的 Heart Rate Measurement（`0x2A37`），支持 8 位及 16 位心率值。它不读取 Garmin Connect 账户或历史数据，也不支持 ANT+。若设备仅通过 ANT+ 广播，或不允许标准 BLE GATT 心率连接，本程序无法接收。

品牌或设备名称不能保证兼容性。请以实际硬件上的扫描、连接和持续数据接收结果为准。
