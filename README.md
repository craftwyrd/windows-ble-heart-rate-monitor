# PulseDesk

PulseDesk 是一个 Windows 实时心率悬浮窗，通过 BLE 扫描并连接标准 Bluetooth Heart Rate Service（服务 `0x180D`，通知特征 `0x2A37`），显示当前 bpm 和最近 60 秒心率曲线。

## 使用

1. 在 Forerunner 265 上开启“广播心率”。
2. 在 Windows 电脑开启蓝牙，然后运行 `dist/PulseDesk.exe`。
3. 点击“扫描”，选择设备。名称可能显示为 Garmin 或未命名设备；标有“心率服务”的设备优先。
4. 点击“连接”。收到心率通知后，界面会显示 bpm 和曲线。

电脑的蓝牙适配器必须支持 BLE。若扫描不到，先将手表和电脑放近，确认广播仍开启；Windows 的位置服务/蓝牙扫描隐私设置也可能影响设备发现。

## 构建

在 Windows PowerShell 中执行：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build.ps1
```

输出为 `dist\PulseDesk.exe`，目标电脑无需安装 Python。脚本使用本机 Python 创建独立虚拟环境。

## 限制

程序只解析 BLE 标准心率通知，不读取 Garmin Connect 账户或历史数据。Garmin 的广播实现、电脑蓝牙驱动和接收端是否允许 GATT 连接，都会影响兼容性；如果 265 只通过 ANT+ 广播，BLE 版本将无法接收。
