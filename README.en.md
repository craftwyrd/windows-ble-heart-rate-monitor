# PulseDesk

[简体中文](README.md) | **English**

A Windows desktop overlay for live heart rate. PulseDesk connects to watches and chest straps over Bluetooth Low Energy (BLE), displaying current heart rate and recent trends without a Garmin Connect login.

Support is based on the standard BLE heart-rate service and subscribable notifications, regardless of brand or device type. Actual connectivity also depends on device broadcast settings, the computer's Bluetooth adapter, and its drivers.

## Features

- Live heart rate in bpm, with a 60-second chart, average, and peak.
- Four views: heart pill, pill with a mini chart, transparent HUD, and full chart and connection panel.
- Borderless, draggable window with always-on-top and opacity controls (100% / 88% / 76%).
- Scan results prioritize and label devices advertising the standard heart-rate service, `0x180D`.
- Retains the last reading when the signal is lost, with text, symbols, and amber styling to indicate stale data.
- System tray controls; receiving continues while the window is hidden.
- A `--demo` mode for previewing the interface and states without a heart-rate device.

## Quick start

### Requirements

- A Windows computer with a BLE-capable Bluetooth adapter and working drivers.
- A device providing the standard BLE Heart Rate Service, with heart-rate broadcasting enabled.
- The packaged EXE runs without Python. Running from source or building requires Python with Tkinter and pip; Python 3.10 or newer is recommended.

### Connect a device

Download the Windows x64 `PulseDesk.exe` from [GitHub Releases](https://github.com/craftwyrd/windows-ble-heart-rate-monitor/releases/latest). The accompanying `SHA256SUMS.txt` provides the file checksum.

The application currently uses Chinese interface labels; their English meanings are included below.

1. Enable your heart-rate device's BLE broadcast or connection mode. For example, enable heart-rate broadcasting on a Forerunner 265 and keep it enabled.
2. Turn on Bluetooth on your computer and launch `PulseDesk.exe`. Local builds place it at `dist\PulseDesk.exe`. If you only have the source, follow the development or build instructions below first.
3. Click **扫描设备** (Scan devices) and select your watch or chest strap. Try devices marked `[心率 0x180D]` first. Unnamed devices show an address suffix; devices without the service label can also be selected manually.
4. Click **连接所选设备** (Connect selected device). After connecting, the window switches to a compact view, initially the heart pill. Live heart rate appears when notifications arrive.
5. Double-click the pill to open the full panel for the 60-second chart and connection controls.

## Views and controls

| View | Size | Content |
| --- | --- | --- |
| Heart pill | 138 × 50 | Heart and current heart rate |
| Pill with chart · Scheme C | 196 × 48 | Heart, heart rate, and the latest 20 samples |
| Transparent HUD | 138 × 50 | Heart and shadowed digits with the background removed |
| Full panel | 316 × 336 / 316 × 176 | Heart rate, 60-second chart, status, and a collapsible connection section |

These are layout dimensions in normal mode. The mini chart shows **20 samples**, which do not necessarily represent 20 seconds.

- **Move the window:** drag the pill or the drag area at the top of the full panel.
- **Change views:** right-click the overlay or use the tray menu. The pill's hover toolbar also opens the full panel or switches to the HUD or mini chart.
- **Hover toolbar:** floats above the pill, or below it near the top of the screen, without moving the heart-rate digits.
- **Always-on-top and opacity:** use the panel buttons, hover toolbar, or window context menu. Always-on-top is enabled by default.
- **System tray:** the heart icon may be inside the `^` overflow area. Double-click to show the window. Right-click to show/hide it, switch among four views, toggle always-on-top, disconnect, or quit.
- **Hide or quit:** **隐藏悬浮窗** (Hide overlay) keeps receiving data in the background. The window's close button or **退出 PulseDesk** (Quit PulseDesk) disconnects and exits the application.

## Preview without a device

Run from source after installing dependencies as described below:

```powershell
.\.venv\Scripts\python.exe app.py --demo
```

Or use the locally built EXE:

```powershell
.\dist\PulseDesk.exe --demo
```

Demo mode starts with a simulated heart-rate pill. Double-click to expand the panel, then use **状态预览** (State preview) in the connection section to preview disconnected, scanning, devices found, connecting, two-/three-digit readings, signal lost, and connection failed states. Simulated readings and state preview buttons are enabled only in this mode.

## Development and build

Run the following commands from the project root in Windows PowerShell.

### Run from source

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

The interface uses Python and Tkinter, BLE communication uses Bleak, and tray icon support uses pystray and Pillow. The application interface is currently in Chinese; this README provides English usage instructions.

### Run tests

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Tests cover heart-rate parsing, malformed notifications, device name display, and Windows UI regressions without a physical Bluetooth device. UI tests require a Windows desktop and are skipped on other platforms. Automated tests do not replace scanning, connection, and notification checks with real hardware.

### Build a single-file EXE

Quit any running instance of PulseDesk first, then run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\build.ps1
```

The script creates or reuses `.venv`, installs dependencies, runs tests, and packages the application with PyInstaller using `PulseDesk.spec`. Failed tests stop packaging. The output, `dist\PulseDesk.exe`, includes the window and EXE icon assets and runs without Python on the target computer.

### Continuous integration and releases

[Windows build and release](https://github.com/craftwyrd/windows-ble-heart-rate-monitor/actions/workflows/build.yml) runs on pushes to `main`, pull requests targeting `main`, and manual dispatch. It uses Windows x64 and Python 3.10.11 to run the same `build.ps1`, execute tests, package the EXE, and check that its demo process stays running. Successful runs provide a `PulseDesk-windows-x64` artifact retained for 14 days.

When a maintainer pushes a `vMAJOR.MINOR.PATCH` tag, the workflow publishes a GitHub Release after validation, attaching `PulseDesk.exe` and `SHA256SUMS.txt`. Release notes come from `docs/releases/<tag>.md` when available, otherwise they are generated automatically. Reruns do not overwrite assets of an already published release. The first basic version is `v0.1.0`.

### Project structure

| Path | Purpose |
| --- | --- |
| `app.py` | Entry point, BLE scanning/connections, notification parsing, and desktop interface |
| `test_app.py` | Heart-rate parsing and device display tests |
| `test_ui.py` | Windows UI regression tests |
| `requirements.txt` | Runtime, test, and packaging dependencies |
| `build.ps1` / `PulseDesk.spec` | Windows build script and PyInstaller configuration |
| `.github/workflows/build.yml` | Windows CI and tagged release workflow |
| `docs/releases/` | Version-specific release notes |
| `assets/` | Icon assets |
| `garmin_hr_overlay.py` / `template/` | Interface prototypes and design references |
| [DESIGN_BRIEF.md](DESIGN_BRIEF.md) | Product goals and design requirements (Chinese) |
| `build/` / `dist/` | Build intermediates and packaged output; do not edit as source |

## Troubleshooting

| Problem | What to try |
| --- | --- |
| No devices found | Confirm Bluetooth is enabled, the adapter supports BLE, and the device is still broadcasting. Move it closer, check Windows Bluetooth drivers and relevant scanning privacy settings, then scan again. |
| No device name or heart-rate label | Check the address suffix on unnamed devices. The `[心率 0x180D]` label comes from advertising data; an unmarked device may still connect, so you can select it manually. |
| Connection timeout or missing `0x2A37` | Confirm the device provides standard BLE heart-rate notifications and keep broadcasting enabled. If another receiver occupies the connection, disconnect that receiver and retry. |
| “信号中断” (Signal lost) | A Bluetooth disconnection or roughly five seconds without new data causes the last value to be retained. Check broadcasting and distance, then reconnect from the full panel. The retained value is not a current live measurement. |
| Window missing after hiding | Double-click the heart icon in the notification area, including the `^` overflow area, or choose **显示悬浮窗** (Show overlay) from its menu. |
| Build asks you to close PulseDesk | Quit through the tray menu. Hiding the window does not end the process. |

## Compatibility and scope

PulseDesk subscribes to Heart Rate Measurement (`0x2A37`) within the standard BLE Heart Rate Service (`0x180D`), supporting 8-bit and 16-bit heart-rate values. It does not read Garmin Connect accounts or historical data and does not support ANT+. Devices broadcasting only over ANT+, or not allowing a standard BLE GATT heart-rate connection, cannot be received by this application.

Brand and device names do not guarantee compatibility. Confirm scanning, connection, and sustained data reception on your actual hardware.
