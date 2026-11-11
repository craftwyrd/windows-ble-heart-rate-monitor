"""PulseDesk desktop monitor for standard Bluetooth Heart Rate devices."""

from __future__ import annotations

import asyncio
import collections
import ctypes
import math
import random
import queue
from pathlib import Path
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import unicodedata
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

ASSET_DIR = Path(__file__).resolve().parent / "assets"
APP_USER_MODEL_ID = "PulseDesk.HeartRateMonitor"


def configure_windows_app_id() -> None:
    """Give source and packaged launches the same Windows taskbar identity."""
    if sys.platform == "win32":
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)


def show_in_taskbar(root: tk.Tk) -> None:
    """Tk's borderless wrapper defaults to a tool window, hidden by Explorer."""
    if sys.platform != "win32":
        return
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    # LONG_PTR must remain pointer-sized on 64-bit Windows.
    get_style = user32.GetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.GetWindowLongW
    set_style = user32.SetWindowLongPtrW if ctypes.sizeof(ctypes.c_void_p) == 8 else user32.SetWindowLongW
    get_style.argtypes = [wintypes.HWND, ctypes.c_int]
    get_style.restype = ctypes.c_ssize_t
    set_style.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_style.restype = ctypes.c_ssize_t
    hwnd = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT: the native Tk wrapper
    ex_style = get_style(hwnd, -20)  # GWL_EXSTYLE
    set_style(hwnd, -20, (ex_style & ~0x00000080) | 0x00040000)

# 尝试导入 Bleak；若当前环境未安装 Bleak，仍可运行 UI 与全状态演示模式 (--demo)
try:
    from bleak import BleakClient, BleakScanner
    from bleak.backends.device import BLEDevice
    from bleak.backends.scanner import AdvertisementData
    BLEAK_AVAILABLE = True
except ImportError:
    BLEAK_AVAILABLE = False
    BLEDevice = object
    AdvertisementData = object


# ============================================================================
# 1. 常量与视觉设计规范 (Design Tokens)
# ============================================================================

HR_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# 严格固定的窗口几何尺寸，防止数字位数变化或长设备名撑破/抖动窗口
MINI_WIDTH = 138
MINI_HEIGHT = 50
SPARK_PILL_WIDTH = 196
SPARK_PILL_HEIGHT = 48
MINI_MODES = ("PILL", "SPARK", "HUD")
MINI_HOVER_BAR_HEIGHT = 24  # 鼠标移入极简模式时顶部浮现的控制条高度

FULL_WIDTH = 316
FULL_COLLAPSED_HEIGHT = 176
FULL_EXPANDED_HEIGHT = 336

# Windows 透明色键（用于 HUD 纯透无框模式与圆角镂空）
CHROMA_KEY_COLOR = "#010203"

PALETTE = {
    "win_bg": "#11151C",          # 主窗口深炭黑底色（浅色/深色壁纸下均具备 >12:1 对比度）
    "win_border": "#2E3846",      # 1.5px 结构外描边，防止浅色壁纸下边缘发虚
    "header_bg": "#171C26",       # 顶部拖动栏背景
    "panel_bg": "#171C26",        # 连接抽屉区背景
    "plot_bg": "#0B0E14",         # 60秒曲线区高阻光深色底
    "plot_grid": "#1E2633",       # 曲线虚线网格
    "text_primary": "#F8FAFC",    # 核心主数字与标题 (纯白)
    "text_secondary": "#94A3B8",  # 次要说明与统计
    "text_muted": "#64748B",      # 占位符与禁用文字
    "control_bg": "#222A38",      # 按钮常驻底色
    "control_hover": "#2E394B",   # 按钮悬停底色
    "accent_primary": "#38BDF8",  # 扫描/连接主交互色 (青蓝)
    "accent_heart": "#F43F5E",    # 实时跳动红心色 (鲜珊瑚红)
    "accent_hr": "#10B981",       # 实时心率曲线绿 (翠绿)
    "status_idle": "#94A3B8",     # 未连接 (中性灰)
    "status_scan": "#38BDF8",     # 扫描中/连接中 (青蓝)
    "status_live": "#10B981",     # 实时接收 (翠绿)
    "status_stale": "#F59E0B",    # 信号中断/数据过期 (琥珀黄)
    "status_error": "#F43F5E",    # 连接失败 (珊瑚红)
}

STATE_CONFIG = {
    "DISCONNECTED": {
        "glyph": "○",
        "label": "未连接",
        "color": PALETTE["status_idle"],
        "badge_bg": "#1E2530",
        "banner": "开启设备的心率广播，点击「扫描设备」搜索附近设备",
    },
    "SCANNING": {
        "glyph": "◎",
        "label": "扫描中…",
        "color": PALETTE["status_scan"],
        "badge_bg": "#122B3D",
        "banner": "正在搜索附近的蓝牙低功耗 (BLE) 设备，优先识别 0x180D…",
    },
    "DEVICES_FOUND": {
        "glyph": "●",
        "label": "发现设备",
        "color": PALETTE["status_scan"],
        "badge_bg": "#122B3D",
        "banner": "已置顶广播 [心率 0x180D] 的设备，请选择心率设备并点击连接",
    },
    "CONNECTING": {
        "glyph": "◔",
        "label": "连接中…",
        "color": PALETTE["status_scan"],
        "badge_bg": "#122B3D",
        "banner": "正在与设备握手并订阅标准心率通知特征 (0x2A37)…",
    },
    "LIVE": {
        "glyph": "●",
        "label": "实时接收",
        "color": PALETTE["status_live"],
        "badge_bg": "#0D2E26",
        "banner": "实时接收中；点击顶部「♥极简」可缩回红心小胶囊",
    },
    "SIGNAL_LOST": {
        "glyph": "▲",
        "label": "信号中断",
        "color": PALETTE["status_stale"],
        "badge_bg": "#33240C",
        "banner": "超 5 秒未收到新数据，保留最后心率值；请靠近或点击重连",
    },
    "CONNECT_FAILED": {
        "glyph": "✕",
        "label": "连接失败",
        "color": PALETTE["status_error"],
        "badge_bg": "#361520",
        "banner": "握手超时或设备未开启心率广播 (0x2A37)，请确认设置后重试",
    },
}


# ============================================================================
# 2. 数据结构与标准 BLE 心率解析 (0x2A37)
# ============================================================================

@dataclass
class ScannedDeviceItem:
    """扫描到的 BLE 设备条目"""
    address: str
    name: str
    rssi: int = -70
    has_hr_service: bool = False
    raw_device: Optional[object] = None

    @property
    def address_suffix(self) -> str:
        clean = self.address.replace("-", ":").split(":")
        if len(clean) >= 2:
            return f"{clean[-2]}:{clean[-1]}".upper()
        return self.address[-5:].upper()

    @property
    def display_title(self) -> str:
        base_name = self.name.strip() if self.name and self.name.strip() else f"未知设备 ({self.address_suffix})"
        width = lambda text: sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)
        if width(base_name) > 19:
            while width(base_name) > 17:
                base_name = base_name[:-1]
            base_name += "…"
        tag = "[心率 0x180D]" if self.has_hr_service else f"[{self.address_suffix}]"
        return f"{base_name}{' ' * max(0, 19 - width(base_name))} {tag}"


def parse_heart_rate(data: bytearray | bytes) -> int:
    """Decode the standard Bluetooth Heart Rate Measurement characteristic."""
    if len(data) < 2:
        raise ValueError("心率通知数据长度不足")
    flags = data[0]
    if flags & 0x01:
        return data[1] | (data[2] << 8) if len(data) >= 3 else _missing_uint16()
    return data[1]


def _missing_uint16() -> int:
    raise ValueError("16 位心率数据不完整")


def parse_ble_heart_rate(data: bytearray | bytes) -> Optional[int]:
    try:
        return parse_heart_rate(data)
    except ValueError:
        return None


# ============================================================================
# 3. 后台 BLE 异步通信线程 (Bleak Worker)
# ============================================================================

class BleHeartRateReceiver:
    """在独立守护线程中运行 asyncio 事件循环，避免阻塞 Tkinter 主线程渲染"""

    def __init__(
        self,
        on_state_change: Callable[[str, str], None],
        on_devices_updated: Callable[[List[ScannedDeviceItem]], None],
        on_hr_sample: Callable[[int, float], None],
    ):
        self.on_state_change = on_state_change
        self.on_devices_updated = on_devices_updated
        self.on_hr_sample = on_hr_sample

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._client: Optional[BleakClient] = None
        self._scanning = False
        self._connecting = False
        self._stop_requested = False
        self._ready = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="BleAsyncWorker")
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()
        pending = asyncio.all_tasks(self._loop)
        for task in pending:
            task.cancel()
        if pending:
            self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        self._loop.close()

    def trigger_scan(self, timeout: float = 4.5) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_scan(timeout), self._loop)

    def trigger_connect(self, device_item: ScannedDeviceItem) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_connect(device_item), self._loop)

    def trigger_disconnect(self) -> None:
        if self._loop:
            asyncio.run_coroutine_threadsafe(self._async_disconnect(), self._loop)

    def shutdown(self) -> None:
        self._stop_requested = True
        if self._loop:
            fut = asyncio.run_coroutine_threadsafe(self._async_disconnect(silent=True), self._loop)
            try:
                fut.result(timeout=1.2)
            except Exception:
                pass
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=1.5)

    async def _async_scan(self, timeout: float) -> None:
        if self._scanning or self._connecting or self._client is not None:
            return
        if not BLEAK_AVAILABLE:
            self.on_state_change(
                "CONNECT_FAILED",
                "未安装 bleak 库。可使用底部「状态预览」体验全部状态，或 pip install bleak 连接真机。",
            )
            return

        self._scanning = True
        self.on_state_change("SCANNING", "正在搜索附近的 BLE 广播设备…")
        try:
            discovered: Dict[str, Tuple[BLEDevice, AdvertisementData]] = await BleakScanner.discover(
                timeout=timeout, return_adv=True
            )
            items: List[ScannedDeviceItem] = []
            for addr, (dev, adv) in discovered.items():
                uuids = [u.lower() for u in (adv.service_uuids or [])]
                has_hr = HR_SERVICE_UUID in uuids
                dev_name = adv.local_name or dev.name or ""
                rssi = getattr(adv, "rssi", -75) or -75
                items.append(
                    ScannedDeviceItem(
                        address=addr,
                        name=dev_name,
                        rssi=rssi,
                        has_hr_service=has_hr,
                        raw_device=dev,
                    )
                )

            items.sort(key=lambda x: (not x.has_hr_service, not bool(x.name), -x.rssi))
            self.on_devices_updated(items)

            if items:
                hr_count = sum(1 for i in items if i.has_hr_service)
                msg = (
                    f"找到 {len(items)} 台设备（含 {hr_count} 台 0x180D 心率设备），请选择并连接"
                    if hr_count > 0
                    else f"找到 {len(items)} 台设备（未检测到 0x180D 标记，仍可手动选择连接）"
                )
                self.on_state_change("DEVICES_FOUND", msg)
            else:
                self.on_state_change(
                    "DISCONNECTED",
                    "未扫描到设备。请确认心率设备已开启 BLE 广播且电脑蓝牙已打开。",
                )
        except Exception as exc:
            self.on_state_change("CONNECT_FAILED", f"蓝牙扫描异常：{str(exc)[:40]}")
        finally:
            self._scanning = False

    async def _async_connect(self, item: ScannedDeviceItem) -> None:
        if self._connecting or self._scanning:
            return
        if not BLEAK_AVAILABLE:
            self.on_state_change("CONNECT_FAILED", "当前环境未安装 bleak 库，无法发起真实 BLE 连接。")
            return

        self._connecting = True
        await self._async_disconnect(silent=True)
        self.on_state_change("CONNECTING", f"正在连接 {item.name or item.address_suffix}…")

        def handle_disconnect(_client: BleakClient) -> None:
            if self._client is _client and not self._stop_requested:
                self._client = None
                self.on_state_change(
                    "SIGNAL_LOST",
                    "蓝牙连接已断开，保留最后心率值；可点击「重新连接」恢复。",
                )

        client = None
        try:
            target = item.raw_device if item.raw_device is not None else item.address
            client = BleakClient(target, disconnected_callback=handle_disconnect, timeout=10.0)
            await client.connect()
            self._client = client

            def notification_handler(_characteristic, data: bytearray) -> None:
                bpm = parse_ble_heart_rate(data)
                if bpm is not None:
                    self.on_hr_sample(bpm, time.time())

            try:
                characteristic = next(
                    (
                        char for service in client.services for char in service.characteristics
                        if char.uuid.lower() == HR_MEASUREMENT_UUID and "notify" in char.properties
                    ),
                    None,
                )
                if characteristic is None:
                    raise ValueError("Missing heart-rate notification characteristic")
                await client.start_notify(characteristic, notification_handler)
            except Exception:
                self._client = None
                await client.disconnect()
                self.on_state_change(
                    "CONNECT_FAILED",
                    "设备未提供标准心率通知 (0x2A37)，请确认支持 BLE 心率服务并已开启广播。",
                )
                return

            self.on_state_change("LIVE", f"已连接 {item.name or item.address_suffix}，等待心率广播…")
        except asyncio.TimeoutError:
            self.on_state_change("CONNECT_FAILED", "连接超时：请确认设备已开启心率广播且未被其他接收端占用。")
        except Exception as exc:
            self.on_state_change("CONNECT_FAILED", f"连接失败 ({type(exc).__name__})，请靠近电脑后重试。")
        finally:
            self._connecting = False
            if client is not None and self._client is not client and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass

    async def _async_disconnect(self, silent: bool = False) -> None:
        client = self._client
        self._client = None
        if client is not None:
            try:
                if client.is_connected:
                    try:
                        await client.stop_notify(HR_MEASUREMENT_UUID)
                    except Exception:
                        pass
                    await client.disconnect()
            except Exception:
                pass
        if not silent and not self._stop_requested:
            self.on_state_change("DISCONNECTED", "已断开设备连接。")


# ============================================================================
# 4. 主桌面悬浮窗视图控制器 (支持 极简红心胶囊 / 纯透贴片 / 完整曲线窗)
# ============================================================================

class HeartRateMonitor:
    """
    支持四种显示模式 (`display_mode`):
      - "PILL": 极简圆角红心胶囊 (138×50px)，左侧跳动红心 + 右侧 3字符等宽数字，悬停顶部浮现微工具栏
      - "SPARK": 方案 C 红心 + 数字 + 最近 20 笔心率尾迹 (196×48px)
      - "HUD" : 纯透无框贴片 (138×50px)，在 Windows 下利用 -transparentcolor 去底框，仅保留红心与带阴影数字
      - "FULL": 完整窗口 (316×336px 展开连接区 / 316×176px 仅保留主数字与 60s 曲线)
    """

    def __init__(self, root: tk.Tk, demo_mode: bool = False):
        self.root = root
        self.demo_mode = demo_mode
        self._closing = False
        self._ui_events = queue.SimpleQueue()
        self.tray_icon = None
        self._last_pill_mode = "PILL"

        # 显示形态: 默认若为演示模式直接进入 "PILL" (极简红心胶囊)，真机首次启动进入 "FULL" 方便选设备，连上后自动缩为 "PILL"
        self.display_mode: str = "PILL" if demo_mode else "FULL"
        self.auto_shrink_on_connect: bool = True

        # 核心业务状态
        self.state_key: str = "DISCONNECTED"
        self.banner_message: str = STATE_CONFIG["DISCONNECTED"]["banner"]
        self.is_topmost: bool = True
        self.opacity_levels: List[float] = [1.0, 0.88, 0.76]
        self.opacity_idx: int = 0
        self.drawer_expanded: bool = True

        # 实时心率缓存: (timestamp, bpm)
        self.hr_history: Deque[Tuple[float, int]] = collections.deque(maxlen=180)
        self.current_bpm: Optional[int] = None
        self.last_sample_ts: Optional[float] = None
        self.connected_device_name: str = "尚未选择"

        # 扫描到的 BLE 设备
        self.scanned_devices: List[ScannedDeviceItem] = []
        self.selected_device_index: int = -1

        # 窗口拖拽偏移与悬停状态
        self._drag_offset_x: int = 0
        self._drag_offset_y: int = 0
        self._mini_hover_active: bool = False
        self._mini_hide_job: Optional[str] = None
        self._heart_beat_phase: int = 0
        self._demo_sim_active: bool = False
        self._supports_transparent_color: bool = False

        # 启动后台 BLE 线程
        self.ble = BleHeartRateReceiver(
            on_state_change=self._threadsafe_state_change,
            on_devices_updated=self._threadsafe_devices_updated,
            on_hr_sample=self._threadsafe_hr_sample,
        )
        self.ble.start()

        self._configure_window()
        self._build_mini_mode_ui()
        self._build_full_mode_ui()
        self._build_context_menu()
        self.plot_canvas.bind("<Configure>", lambda _event: self._redraw_sparkline())

        if self.demo_mode:
            self.load_preset_state("LIVE_2DIGIT", keep_current_mode=True)
        else:
            self.set_display_mode("FULL")
            self._apply_state_visuals()

        self.root.after(50, self._drain_ui_events)
        self._schedule_periodic_refresh()
        self._schedule_heart_animation()
        self._start_tray()

    # ------------------------------------------------------------------------
    # 窗口底层属性设置
    # ------------------------------------------------------------------------
    def _configure_window(self) -> None:
        self.root.title("PulseDesk · BLE 心率")
        self.root.geometry("+140+140")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close_app)
        self.root.bind("<Escape>", lambda _event: self.close_app())
        self.root.iconbitmap(default=str(ASSET_DIR / "pulsedesk.ico"))
        self._icon_image = tk.PhotoImage(master=self.root, file=str(ASSET_DIR / "pulsedesk.png"))
        self.root.iconphoto(True, self._icon_image)
        self.root.bind("<Map>", self._on_window_mapped, add="+")
        self.root.bind("<Configure>", self._on_window_configured, add="+")
        self.root.configure(bg=PALETTE["win_bg"])
        self.root.overrideredirect(True)
        try:
            self.root.wm_attributes("-topmost", self.is_topmost)
            self.root.wm_attributes("-alpha", self.opacity_levels[self.opacity_idx])
        except Exception:
            pass
        # 检测当前系统是否支持 Windows -transparentcolor (用于 HUD 纯透模式与胶囊圆角)
        try:
            self.root.wm_attributes("-transparentcolor", CHROMA_KEY_COLOR)
            self._supports_transparent_color = True
        except Exception:
            self._supports_transparent_color = False

    def _on_window_mapped(self, event: tk.Event) -> None:
        if event.widget is self.root and not self._closing:
            # Tk may recreate its native wrapper while mapping the first view.
            self.root.after_idle(self._apply_window_identity)

    def _apply_window_identity(self) -> None:
        self.root.iconbitmap(str(ASSET_DIR / "pulsedesk.ico"))
        show_in_taskbar(self.root)

    def _start_tray(self) -> None:
        if sys.platform != "win32":
            return
        import pystray
        from PIL import Image

        def action(callback, *args):
            # pystray owns a separate message loop; all Tk actions stay on Tk's thread.
            def invoke(_icon, _item):
                if not self._closing:
                    self._ui_events.put((callback, args))
            return invoke

        def mode_item(label, mode):
            return pystray.MenuItem(
                label, action(self._show_mode, mode), radio=True,
                checked=lambda _item: self.display_mode == mode,
            )

        menu = pystray.Menu(
            pystray.MenuItem("显示悬浮窗", action(self.show_window), default=True),
            pystray.MenuItem("隐藏悬浮窗", action(self.hide_window)),
            pystray.Menu.SEPARATOR,
            mode_item("极简红心胶囊", "PILL"),
            mode_item("极简带曲线 · 方案 C", "SPARK"),
            mode_item("透明 HUD", "HUD"),
            mode_item("完整曲线与连接面板", "FULL"),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("窗口置顶", action(self.toggle_topmost),
                             checked=lambda _item: self.is_topmost),
            pystray.MenuItem("断开连接", action(self.on_click_disconnect)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出 PulseDesk", action(self.close_app)),
        )
        with Image.open(ASSET_DIR / "pulsedesk.png") as image:
            tray_image = image.copy()
        self.tray_icon = pystray.Icon("PulseDesk", tray_image, "PulseDesk · BLE 心率", menu)
        self.tray_icon.run_detached()

    def _show_mode(self, mode: str) -> None:
        self.set_display_mode(mode)
        self.show_window()

    def show_window(self) -> None:
        if not self._closing:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()

    def hide_window(self) -> None:
        self._hide_mini_hover_bar()
        self.root.withdraw()

    def _on_window_configured(self, event: tk.Event) -> None:
        if event.widget is self.root and self._mini_hover_active:
            self._position_mini_hover_bar()

    def _sync_window_geometry(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        if self.display_mode in MINI_MODES:
            w, h = ((SPARK_PILL_WIDTH, SPARK_PILL_HEIGHT)
                    if self.display_mode == "SPARK" else (MINI_WIDTH, MINI_HEIGHT))
        else:
            w = FULL_WIDTH
            h = FULL_EXPANDED_HEIGHT if self.drawer_expanded else FULL_COLLAPSED_HEIGHT

        if x is None or y is None:
            x, y = self.root.winfo_x(), self.root.winfo_y()
        if self.demo_mode and self.display_mode == "FULL" and self.drawer_expanded:
            h += 24  # Two rows of preview controls, outside the production UI.
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------------
    # 构建极简「红心 + 数字」视图 (Mini Mode: PILL & HUD)
    # ------------------------------------------------------------------------
    def _build_mini_mode_ui(self) -> None:
        bg_col = CHROMA_KEY_COLOR if self._supports_transparent_color else PALETTE["win_bg"]
        self.mini_container = tk.Frame(self.root, bg=bg_col, bd=0, highlightthickness=0)

        # 独立浮层不参与胶囊布局，悬停时不会挤动红心或心率数字。
        self.mini_toolbar = tk.Toplevel(self.root)
        self.mini_toolbar.withdraw()
        self.mini_toolbar.overrideredirect(True)
        self.mini_toolbar.transient(self.root)
        self.mini_toolbar.attributes("-topmost", self.is_topmost)
        self.mini_toolbar.bind("<Enter>", self._on_mini_mouse_enter)
        self.mini_toolbar.bind("<Leave>", self._on_mini_mouse_leave)
        self.mini_hover_bar = tk.Frame(
            self.mini_toolbar,
            bg="#0F172A",
            height=MINI_HOVER_BAR_HEIGHT,
            highlightbackground="#334155",
            highlightthickness=1,
        )
        self.mini_hover_bar.pack(fill=tk.BOTH, expand=True)
        self.mini_hover_bar.pack_propagate(False)

        btn_cfg = {
            "bg": "#0F172A",
            "activebackground": "#1E293B",
            "bd": 0,
            "relief": tk.FLAT,
            "font": ("Segoe UI", 8, "bold"),
            "padx": 4,
            "pady": 0,
            "cursor": "hand2",
        }
        tk.Button(
            self.mini_hover_bar,
            text="展开",
            fg=PALETTE["accent_primary"],
            activeforeground="#FFFFFF",
            command=lambda: self.set_display_mode("FULL"),
            **btn_cfg,
        ).pack(side=tk.LEFT, padx=(4, 1))

        self.btn_mini_hud = tk.Button(
            self.mini_hover_bar,
            text="纯透",
            fg=PALETTE["text_secondary"],
            activeforeground="#FFFFFF",
            command=self.toggle_pill_or_hud,
            **btn_cfg,
        )
        self.btn_mini_hud.pack(side=tk.LEFT, padx=1)

        self.btn_mini_spark = tk.Button(
            self.mini_hover_bar,
            text="曲线",
            fg=PALETTE["accent_hr"],
            activeforeground="#FFFFFF",
            command=lambda: self.set_display_mode("PILL" if self.display_mode == "SPARK" else "SPARK"),
            **btn_cfg,
        )
        self.btn_mini_spark.pack(side=tk.LEFT, padx=1)

        self.btn_mini_opacity = tk.Button(
            self.mini_hover_bar,
            text="100%",
            fg=PALETTE["text_secondary"],
            activeforeground="#FFFFFF",
            command=self.cycle_opacity,
            **btn_cfg,
        )
        self.btn_mini_opacity.pack(side=tk.LEFT, padx=1)

        tk.Button(
            self.mini_hover_bar,
            text="✕",
            fg=PALETTE["text_secondary"],
            activeforeground=PALETTE["status_error"],
            command=self.close_app,
            **btn_cfg,
        ).pack(side=tk.RIGHT, padx=(1, 4))

        # 2. 核心「红心 + 数字」画布 (138 × 50 px)
        self.mini_canvas = tk.Canvas(
            self.mini_container,
            width=MINI_WIDTH,
            height=MINI_HEIGHT,
            bg=bg_col,
            bd=0,
            highlightthickness=0,
            cursor="fleur",
        )
        self.mini_canvas.pack(fill=tk.BOTH, expand=True)

        # 事件绑定：左键拖动、双击展开完整面板、右键菜单、鼠标移入移出控制微工具条
        self.mini_canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.mini_canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.mini_canvas.bind("<Double-Button-1>", lambda _e: self.set_display_mode("FULL"))
        self.mini_canvas.bind("<Button-3>", self._show_context_menu)

        self.mini_container.bind("<Enter>", self._on_mini_mouse_enter)
        self.mini_container.bind("<Leave>", self._on_mini_mouse_leave)

    def _on_mini_mouse_enter(self, _event=None) -> None:
        if self._closing or self.display_mode not in MINI_MODES:
            return
        if self._mini_hide_job is not None:
            self.root.after_cancel(self._mini_hide_job)
            self._mini_hide_job = None
        self._mini_hover_active = True
        self._position_mini_hover_bar()
        self.mini_toolbar.deiconify()
        self.mini_toolbar.lift(self.root)

    def _position_mini_hover_bar(self) -> None:
        if not self._mini_hover_active or self._closing:
            return
        # 请求宽度取自按钮，避免紧凑工具条中的文字被裁切。
        width = max(MINI_WIDTH, sum(w.winfo_reqwidth() + 4 for w in self.mini_hover_bar.winfo_children()) + 8)
        x = self.root.winfo_rootx() + (self.root.winfo_width() - width) // 2
        y = self.root.winfo_rooty() - MINI_HOVER_BAR_HEIGHT - 3
        if y < self.root.winfo_vrooty():
            y = self.root.winfo_rooty() + self.root.winfo_height() + 3
        self.mini_toolbar.geometry(f"{width}x{MINI_HOVER_BAR_HEIGHT}+{x}+{y}")

    def _on_mini_mouse_leave(self, _event=None) -> None:
        if self._closing or not self._mini_hover_active:
            return
        if self._mini_hide_job is not None:
            self.root.after_cancel(self._mini_hide_job)
        # 留出跨越胶囊和浮层间隙的时间，方便点击工具条按钮。
        self._mini_hide_job = self.root.after(180, self._hide_mini_hover_if_outside)

    def _hide_mini_hover_if_outside(self) -> None:
        self._mini_hide_job = None
        x, y = self.root.winfo_pointerxy()
        for window in (self.root, self.mini_toolbar):
            wx, wy = window.winfo_rootx(), window.winfo_rooty()
            if wx <= x < wx + window.winfo_width() and wy <= y < wy + window.winfo_height():
                return
        self._hide_mini_hover_bar()

    def _hide_mini_hover_bar(self) -> None:
        if self._mini_hide_job is not None:
            self.root.after_cancel(self._mini_hide_job)
            self._mini_hide_job = None
        self._mini_hover_active = False
        self.mini_toolbar.withdraw()

    # ------------------------------------------------------------------------
    # 构建完整曲线与蓝牙连接管理视图 (Full Mode: 316px 定宽)
    # ------------------------------------------------------------------------
    def _build_full_mode_ui(self) -> None:
        self.full_container = tk.Frame(
            self.root,
            bg=PALETTE["win_bg"],
            highlightbackground=PALETTE["win_border"],
            highlightthickness=1,
        )

        # ---- A. 顶部拖动栏与控制按钮 (Height: 28px) ----
        self.header_bar = tk.Frame(self.full_container, bg=PALETTE["header_bg"], height=28)
        self.header_bar.pack(fill=tk.X, side=tk.TOP)
        self.header_bar.pack_propagate(False)

        self.drag_handle = tk.Label(
            self.header_bar,
            text="♥ BLE HR",
            bg=PALETTE["header_bg"],
            fg=PALETTE["text_secondary"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
            padx=6,
            cursor="fleur",
        )
        self.drag_handle.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.drag_handle.bind("<ButtonPress-1>", self._on_drag_start)
        self.drag_handle.bind("<B1-Motion>", self._on_drag_motion)
        self.drag_handle.bind("<Button-3>", self._show_context_menu)

        self.status_pill = tk.Label(
            self.header_bar,
            text="○ 未连接",
            bg="#1E2530",
            fg=PALETTE["status_idle"],
            font=("Segoe UI", 8, "bold"),
            padx=2,
            pady=1,
        )
        self.status_pill.pack(side=tk.LEFT, padx=(0, 4), pady=4)
        self.status_pill.bind("<ButtonPress-1>", self._on_drag_start)
        self.status_pill.bind("<B1-Motion>", self._on_drag_motion)

        self.btn_close = tk.Button(
            self.header_bar,
            text="✕",
            command=self.close_app,
            bg=PALETTE["header_bg"],
            fg=PALETTE["text_secondary"],
            activebackground=PALETTE["status_error"],
            activeforeground="#FFFFFF",
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=6,
            cursor="hand2",
        )
        self.btn_close.pack(side=tk.RIGHT, fill=tk.Y)

        self.btn_drawer = tk.Button(
            self.header_bar,
            text="▲收起",
            command=self.toggle_drawer,
            bg=PALETTE["header_bg"],
            fg=PALETTE["accent_primary"],
            activebackground=PALETTE["control_hover"],
            activeforeground=PALETTE["text_primary"],
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            padx=2,
            cursor="hand2",
        )
        self.btn_drawer.pack(side=tk.RIGHT, fill=tk.Y)

        self.btn_pin = tk.Button(
            self.header_bar,
            text="置顶",
            command=self.toggle_topmost,
            bg=PALETTE["header_bar"] if "header_bar" in PALETTE else PALETTE["header_bg"],
            fg=PALETTE["text_primary"],
            activebackground=PALETTE["control_hover"],
            activeforeground=PALETTE["text_primary"],
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            padx=2,
            cursor="hand2",
        )
        self.btn_pin.pack(side=tk.RIGHT, fill=tk.Y)

        self.btn_opacity = tk.Button(
            self.header_bar,
            text="100%",
            command=self.cycle_opacity,
            bg=PALETTE["header_bg"],
            fg=PALETTE["text_secondary"],
            activebackground=PALETTE["control_hover"],
            activeforeground=PALETTE["text_primary"],
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            padx=2,
            cursor="hand2",
        )
        self.btn_opacity.pack(side=tk.RIGHT, fill=tk.Y)

        # 一键切换为「♥极简红心胶囊」按钮
        self.btn_to_mini = tk.Button(
            self.header_bar,
            text="♥极简",
            command=lambda: self.set_display_mode(self._last_pill_mode),
            bg=PALETTE["header_bg"],
            fg=PALETTE["accent_heart"],
            activebackground=PALETTE["control_hover"],
            activeforeground="#FFFFFF",
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=3,
            cursor="hand2",
        )
        self.btn_to_mini.pack(side=tk.RIGHT, fill=tk.Y)

        # ---- B. 核心数据主视图 (固定高度 64px，3字符槽位防止2/3位数跳动) ----
        self.hero_frame = tk.Frame(self.full_container, bg=PALETTE["win_bg"], height=64)
        self.hero_frame.pack(fill=tk.X, padx=10, pady=(2, 0))
        self.hero_frame.pack_propagate(False)

        self.bpm_readout_frame = tk.Frame(self.hero_frame, bg=PALETTE["win_bg"], width=152, height=60)
        self.bpm_readout_frame.pack(side=tk.LEFT, fill=tk.Y)
        self.bpm_readout_frame.pack_propagate(False)

        self.lbl_full_heart = tk.Label(
            self.bpm_readout_frame,
            text="♥",
            bg=PALETTE["win_bg"],
            fg=PALETTE["accent_heart"],
            font=("Segoe UI", 18, "bold"),
        )
        self.lbl_full_heart.pack(side=tk.LEFT, padx=(0, 2), pady=(6, 0))

        self.lbl_bpm_value = tk.Label(
            self.bpm_readout_frame,
            text="---",
            bg=PALETTE["win_bg"],
            fg=PALETTE["text_primary"],
            font=("Consolas", 34, "bold"),
            width=3,
            anchor="e",
        )
        self.lbl_bpm_value.pack(side=tk.LEFT, pady=(2, 0))

        self.lbl_bpm_unit = tk.Label(
            self.bpm_readout_frame,
            text="bpm",
            bg=PALETTE["win_bg"],
            fg=PALETTE["text_secondary"],
            font=("Segoe UI", 10, "bold"),
            anchor="sw",
        )
        self.lbl_bpm_unit.pack(side=tk.LEFT, padx=(4, 0), pady=(22, 0))

        self.meta_frame = tk.Frame(self.hero_frame, bg=PALETTE["win_bg"])
        self.meta_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, pady=(6, 2))

        self.lbl_freshness = tk.Label(
            self.meta_frame,
            text="等待心率广播…",
            bg=PALETTE["win_bg"],
            fg=PALETTE["text_secondary"],
            font=("Segoe UI", 8),
            anchor="e",
        )
        self.lbl_freshness.pack(fill=tk.X)

        self.lbl_secondary_stats = tk.Label(
            self.meta_frame,
            text="60s 均值 --- · 最高 ---",
            bg=PALETTE["win_bg"],
            fg=PALETTE["text_muted"],
            font=("Consolas", 8),
            anchor="e",
        )
        self.lbl_secondary_stats.pack(fill=tk.X, pady=(2, 0))

        self.lbl_device_caption = tk.Label(
            self.meta_frame,
            text="设备: 尚未选择",
            bg=PALETTE["win_bg"],
            fg=PALETTE["text_muted"],
            font=("Segoe UI", 8),
            anchor="e",
        )
        self.lbl_device_caption.pack(fill=tk.X, pady=(1, 0))

        # ---- C. 最近 60 秒实时心率曲线画布 (固定高度 72px) ----
        self.plot_canvas = tk.Canvas(
            self.full_container,
            bg=PALETTE["plot_bg"],
            height=70,
            bd=0,
            highlightthickness=1,
            highlightbackground="#1E2633",
        )
        self.plot_canvas.pack(fill=tk.X, padx=10, pady=(2, 6))

        # ---- D. 可收起/展开的 BLE 连接管理抽屉 ----
        self.drawer_frame = tk.Frame(
            self.full_container,
            bg=PALETTE["panel_bg"],
            highlightbackground="#263040",
            highlightthickness=1,
        )
        self.drawer_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))

        self.lbl_banner = tk.Label(
            self.drawer_frame,
            text=self.banner_message,
            bg="#1C2330",
            fg=PALETTE["text_secondary"],
            font=("Segoe UI", 8),
            wraplength=276,
            justify=tk.LEFT,
            anchor="w",
            padx=6,
            pady=4,
            height=2,
        )
        self.lbl_banner.pack(fill=tk.X, padx=6, pady=(6, 4))

        list_container = tk.Frame(self.drawer_frame, bg=PALETTE["panel_bg"])
        list_container.pack(fill=tk.X, padx=6, pady=(0, 4))

        self.device_listbox = tk.Listbox(
            list_container,
            height=3,
            bg="#0E1218",
            fg=PALETTE["text_primary"],
            selectbackground="#1E3A5F",
            selectforeground="#FFFFFF",
            highlightthickness=1,
            highlightbackground="#283242",
            bd=0,
            font=("Consolas", 8),
            activestyle="none",
            exportselection=False,
        )
        self.device_listbox.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.device_listbox.bind("<<ListboxSelect>>", self._on_device_select)

        scrollbar = tk.Scrollbar(list_container, orient=tk.VERTICAL, command=self.device_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.device_listbox.config(yscrollcommand=scrollbar.set)

        action_bar = tk.Frame(self.drawer_frame, bg=PALETTE["panel_bg"])
        action_bar.pack(fill=tk.X, padx=6, pady=(2, 4))

        self.btn_scan = tk.Button(
            action_bar,
            text="↻ 扫描设备",
            command=self.on_click_scan,
            bg=PALETTE["control_bg"],
            fg=PALETTE["text_primary"],
            activebackground=PALETTE["control_hover"],
            activeforeground="#FFFFFF",
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=8,
            pady=4,
            cursor="hand2",
        )
        self.btn_scan.pack(side=tk.LEFT, padx=(0, 4))

        self.btn_connect = tk.Button(
            action_bar,
            text="连接所选设备",
            command=self.on_click_connect,
            bg="#0284C7",
            fg="#FFFFFF",
            activebackground="#0369A1",
            activeforeground="#FFFFFF",
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=10,
            pady=4,
            cursor="hand2",
        )
        self.btn_connect.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))

        self.btn_disconnect = tk.Button(
            action_bar,
            text="断开",
            command=self.on_click_disconnect,
            bg=PALETTE["control_bg"],
            fg=PALETTE["text_secondary"],
            activebackground=PALETTE["status_error"],
            activeforeground="#FFFFFF",
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            padx=8,
            pady=4,
            cursor="hand2",
        )
        self.btn_disconnect.pack(side=tk.RIGHT)

        self._attach_tooltip(self.btn_close, "断开连接并关闭 PulseDesk")
        self._attach_tooltip(self.btn_to_mini, "切换极简红心胶囊；双击胶囊可展开")
        self._attach_tooltip(self.btn_pin, "切换窗口置顶")
        self._attach_tooltip(self.lbl_device_caption, lambda: self.connected_device_name)

        if not self.demo_mode:
            return

        # 底部状态快速验收预览栏（仅 --demo）
        sim_bar = tk.Frame(self.drawer_frame, bg=PALETTE["panel_bg"])
        sim_bar.pack(fill=tk.X, padx=6, pady=(0, 4))

        tk.Label(
            sim_bar,
            text="状态预览:",
            bg=PALETTE["panel_bg"],
            fg=PALETTE["text_muted"],
            font=("Segoe UI", 7),
        ).grid(row=0, column=0, columnspan=4, sticky="w")

        presets = [
            ("未连接", "DISCONNECTED"),
            ("扫描", "SCANNING"),
            ("有设备", "DEVICES_FOUND"),
            ("连接中", "CONNECTING"),
            ("2位数", "LIVE_2DIGIT"),
            ("3位数", "LIVE_3DIGIT"),
            ("中断", "SIGNAL_LOST"),
            ("失败", "CONNECT_FAILED"),
        ]
        for index, (title, code) in enumerate(presets):
            tk.Button(
                sim_bar,
                text=title,
                command=lambda c=code: self.load_preset_state(c, keep_current_mode=True),
                bg="#131820",
                fg=PALETTE["text_secondary"],
                activebackground=PALETTE["control_hover"],
                activeforeground="#FFFFFF",
                bd=0,
                relief=tk.FLAT,
                font=("Segoe UI", 7),
                padx=3,
                pady=1,
                cursor="hand2",
            ).grid(row=1 + index // 4, column=index % 4, sticky="ew", padx=1)

    def _attach_tooltip(self, widget: tk.Widget, text) -> None:
        popup = None

        def hide(_event=None):
            nonlocal popup
            if popup is not None:
                popup.destroy()
                popup = None

        def show(_event):
            nonlocal popup
            if popup is not None or self._closing:
                return
            popup = tk.Toplevel(self.root)
            popup.overrideredirect(True)
            popup.attributes("-topmost", self.is_topmost)
            tk.Label(popup, text=text() if callable(text) else text,
                     bg=PALETTE["control_bg"], fg=PALETTE["text_primary"],
                     font=("Segoe UI", 8), padx=7, pady=4).pack()
            popup.geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 3}")

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")
        widget.bind("<ButtonPress>", hide, add="+")

    # ------------------------------------------------------------------------
    # 右键快捷菜单 (方便在极简模式下也能快速切换形态、置顶、透明度或退出)
    # ------------------------------------------------------------------------
    def _build_context_menu(self) -> None:
        self.ctx_menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#171C26",
            fg="#F8FAFC",
            activebackground="#0284C7",
            activeforeground="#FFFFFF",
            font=("Segoe UI", 9),
        )
        self.ctx_menu.add_command(label="♥ 极简红心胶囊 (138×50)", command=lambda: self.set_display_mode("PILL"))
        self.ctx_menu.add_command(label="♥ 极简带曲线 · 方案 C (196×48)", command=lambda: self.set_display_mode("SPARK"))
        self.ctx_menu.add_command(label="♥ 纯透无框贴片 (HUD 模式)", command=lambda: self.set_display_mode("HUD"))
        self.ctx_menu.add_command(label="📈 展开 60s 曲线与蓝牙连接窗", command=lambda: self.set_display_mode("FULL"))
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="📌 切换窗口置顶", command=self.toggle_topmost)
        self.ctx_menu.add_command(label="◐ 切换透明度 (100% / 88% / 76%)", command=self.cycle_opacity)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="✕ 断开并关闭程序", command=self.close_app)

    def _show_context_menu(self, event: tk.Event) -> None:
        try:
            self.ctx_menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.ctx_menu.grab_release()

    # ------------------------------------------------------------------------
    # 视图形态切换与窗口交互
    # ------------------------------------------------------------------------
    def set_display_mode(self, mode: str) -> None:
        if mode not in (*MINI_MODES, "FULL"):
            return
        self.display_mode = mode
        if mode in ("PILL", "SPARK"):
            self._last_pill_mode = mode
        if mode in MINI_MODES:
            self.full_container.pack_forget()
            self.mini_container.pack(fill=tk.BOTH, expand=True)
            self.btn_mini_hud.config(
                text="胶囊" if mode == "HUD" else "纯透",
                fg=PALETTE["accent_primary"] if mode == "HUD" else PALETTE["text_secondary"],
            )
            self.btn_mini_spark.config(text="简洁" if mode == "SPARK" else "曲线")
        else:
            self._hide_mini_hover_bar()
            self.mini_container.pack_forget()
            self.full_container.pack(fill=tk.BOTH, expand=True)
        self._sync_window_geometry()
        self._apply_state_visuals()

    def toggle_pill_or_hud(self) -> None:
        next_mode = self._last_pill_mode if self.display_mode == "HUD" else "HUD"
        self.set_display_mode(next_mode)

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_offset_x = event.x_root - self.root.winfo_x()
        self._drag_offset_y = event.y_root - self.root.winfo_y()

    def _on_drag_motion(self, event: tk.Event) -> None:
        new_x = event.x_root - self._drag_offset_x
        new_y = event.y_root - self._drag_offset_y
        self._sync_window_geometry(x=new_x, y=new_y)

    def toggle_topmost(self) -> None:
        self.is_topmost = not self.is_topmost
        try:
            self.root.wm_attributes("-topmost", self.is_topmost)
            self.mini_toolbar.attributes("-topmost", self.is_topmost)
        except Exception:
            pass
        self.btn_pin.config(
            text="置顶" if self.is_topmost else "普通",
            fg=PALETTE["text_primary"] if self.is_topmost else PALETTE["text_muted"],
        )
        show_in_taskbar(self.root)

    def cycle_opacity(self) -> None:
        self.opacity_idx = (self.opacity_idx + 1) % len(self.opacity_levels)
        alpha = self.opacity_levels[self.opacity_idx]
        try:
            self.root.wm_attributes("-alpha", alpha)
            self.mini_toolbar.attributes("-alpha", alpha)
        except Exception:
            pass
        pct_text = f"{int(alpha * 100)}%"
        self.btn_opacity.config(text=pct_text)
        self.btn_mini_opacity.config(text=pct_text)
        show_in_taskbar(self.root)

    def toggle_drawer(self) -> None:
        self.set_drawer_expanded(not self.drawer_expanded)

    def set_drawer_expanded(self, expanded: bool) -> None:
        self.drawer_expanded = expanded
        if self.drawer_expanded:
            self.drawer_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 8))
            self.btn_drawer.config(text="▲收起")
        else:
            self.drawer_frame.pack_forget()
            self.btn_drawer.config(text="▼连接")
        if self.display_mode == "FULL":
            self._sync_window_geometry()

    def close_app(self) -> None:
        if self._closing:
            return
        self._hide_mini_hover_bar()
        self._closing = True
        self._demo_sim_active = False
        if self.tray_icon is not None:
            self.tray_icon.stop()
            self.tray_icon = None
        self.ble.shutdown()
        self.root.destroy()

    # ------------------------------------------------------------------------
    # BLE 操作与线程安全状态同步
    # ------------------------------------------------------------------------
    def _on_device_select(self, _event=None) -> None:
        sel = self.device_listbox.curselection()
        if sel:
            self.selected_device_index = int(sel[0])

    def on_click_scan(self) -> None:
        if self.state_key in ("SCANNING", "CONNECTING") or self.ble._client is not None:
            return
        self._demo_sim_active = False
        self._set_state("SCANNING")
        self.ble.trigger_scan(timeout=4.5)

    def on_click_connect(self) -> None:
        if self.state_key in ("SCANNING", "CONNECTING"):
            return
        self._demo_sim_active = False
        if not self.scanned_devices:
            self._set_state("CONNECT_FAILED", "请先扫描并选择支持标准 BLE 心率服务的设备。")
            return
        idx = self.selected_device_index if 0 <= self.selected_device_index < len(self.scanned_devices) else 0
        target = self.scanned_devices[idx]
        self.connected_device_name = target.name or f"BLE {target.address_suffix}"
        self.current_bpm = None
        self.last_sample_ts = None
        self.hr_history.clear()
        self._set_state("CONNECTING")
        self.ble.trigger_connect(target)

    def on_click_disconnect(self) -> None:
        self._demo_sim_active = False
        self.ble.trigger_disconnect()

    def _threadsafe_state_change(self, new_state: str, banner_msg: str) -> None:
        self._ui_events.put((self._set_state, (new_state, banner_msg)))

    def _threadsafe_devices_updated(self, devices: List[ScannedDeviceItem]) -> None:
        self._ui_events.put((self._update_device_listbox, (devices,)))

    def _threadsafe_hr_sample(self, bpm: int, ts: float) -> None:
        self._ui_events.put((self._ingest_hr_sample, (bpm, ts)))

    def _drain_ui_events(self) -> None:
        if self._closing:
            return
        while not self._ui_events.empty():
            callback, args = self._ui_events.get_nowait()
            callback(*args)
        self.root.after(50, self._drain_ui_events)

    def _set_state(self, new_state: str, banner_msg: Optional[str] = None) -> None:
        prev_state = self.state_key
        self.state_key = new_state
        cfg = STATE_CONFIG.get(new_state, STATE_CONFIG["DISCONNECTED"])
        self.banner_message = banner_msg if banner_msg is not None else cfg["banner"]

        # 真机连接成功后若开启了自动折叠，自动切换为「极简红心胶囊」
        if (
            new_state == "LIVE"
            and prev_state == "CONNECTING"
            and self.auto_shrink_on_connect
            and not self.demo_mode
        ):
            self.set_display_mode(self._last_pill_mode)
            return

        self._apply_state_visuals()

    def _update_device_listbox(self, devices: List[ScannedDeviceItem]) -> None:
        self.scanned_devices = devices
        self.selected_device_index = -1
        self.device_listbox.delete(0, tk.END)
        for item in devices:
            self.device_listbox.insert(tk.END, item.display_title)
        if devices:
            self.selected_device_index = 0
            self.device_listbox.selection_set(0)

    def _ingest_hr_sample(self, bpm: int, ts: float) -> None:
        self.current_bpm = bpm
        self.last_sample_ts = ts
        self.hr_history.append((ts, bpm))
        cutoff = ts - 62.0
        while self.hr_history and self.hr_history[0][0] < cutoff:
            self.hr_history.popleft()
        if self.state_key != "LIVE":
            self._set_state("LIVE", f"正在接收 {self.connected_device_name} 的实时广播")
        else:
            self._apply_state_visuals()

    # ------------------------------------------------------------------------
    # 视觉状态同步与重绘 (同时驱动极简红心画布与完整窗口)
    # ------------------------------------------------------------------------
    def _apply_state_visuals(self) -> None:
        # 1. 重绘极简「红心 + 数字」画布
        self._redraw_mini_heart_canvas()

        # 2. 更新完整窗口控件
        cfg = STATE_CONFIG.get(self.state_key, STATE_CONFIG["DISCONNECTED"])
        self.status_pill.config(
            text=f"{cfg['glyph']} {cfg['label']}",
            fg=cfg["color"],
            bg=cfg["badge_bg"],
        )

        if self.state_key == "LIVE" and self.current_bpm is not None:
            self.lbl_bpm_value.config(text=f"{self.current_bpm:>3d}", fg=PALETTE["text_primary"])
            self.lbl_bpm_unit.config(fg=PALETTE["accent_hr"])
            self.lbl_full_heart.config(fg=PALETTE["accent_heart"])
        elif self.state_key == "SIGNAL_LOST" and self.current_bpm is not None:
            self.lbl_bpm_value.config(text=f"{self.current_bpm:>3d}", fg=PALETTE["status_stale"])
            self.lbl_bpm_unit.config(fg=PALETTE["status_stale"])
            self.lbl_full_heart.config(fg=PALETTE["status_stale"])
        else:
            self.lbl_bpm_value.config(text="---", fg=PALETTE["text_muted"])
            self.lbl_bpm_unit.config(fg=PALETTE["text_muted"])
            self.lbl_full_heart.config(fg=PALETTE["text_muted"])

        now = time.time()
        if self.state_key == "LIVE" and self.last_sample_ts is not None:
            age = max(0.0, now - self.last_sample_ts)
            if age < 2.0:
                self.lbl_freshness.config(text="● 刚刚更新 (<1s)", fg=PALETTE["accent_hr"])
            else:
                self.lbl_freshness.config(text=f"● {int(age)} 秒前更新", fg=PALETTE["text_secondary"])
        elif self.state_key == "SIGNAL_LOST" and self.last_sample_ts is not None:
            age = max(5, int(now - self.last_sample_ts))
            self.lbl_freshness.config(text=f"▲ 数据已过期 ({age}s 前)", fg=PALETTE["status_stale"])
        elif self.state_key == "SCANNING":
            self.lbl_freshness.config(text="◎ 正在扫描 BLE 广播…", fg=PALETTE["status_scan"])
        elif self.state_key == "CONNECTING":
            self.lbl_freshness.config(text="◔ 正在订阅 0x2A37…", fg=PALETTE["status_scan"])
        elif self.state_key == "CONNECT_FAILED":
            self.lbl_freshness.config(text="✕ 未能建立心率订阅", fg=PALETTE["status_error"])
        else:
            self.lbl_freshness.config(text="○ 暂无实时数据", fg=PALETTE["text_muted"])

        recent_bpms = [b for (t, b) in self.hr_history if now - t <= 60.0]
        if recent_bpms:
            avg_bpm = int(round(sum(recent_bpms) / len(recent_bpms)))
            max_bpm = max(recent_bpms)
            self.lbl_secondary_stats.config(
                text=f"60s 均值 {avg_bpm:>3d} · 最高 {max_bpm:>3d}",
                fg=PALETTE["text_secondary"],
            )
        else:
            self.lbl_secondary_stats.config(
                text="60s 均值 --- · 最高 ---",
                fg=PALETTE["text_muted"],
            )

        caption = f"设备: {self.connected_device_name}"
        font = tkfont.Font(root=self.root, font=self.lbl_device_caption.cget("font"))
        available = max(130, self.meta_frame.winfo_width() - 4)
        if font.measure(caption) > available:
            while caption and font.measure(caption + "…") > available:
                caption = caption[:-1]
            caption += "…"
        self.lbl_device_caption.config(text=caption)

        banner_text = self.banner_message or cfg["banner"] or "已就绪"
        banner_fg = (
            PALETTE["status_error"]
            if self.state_key == "CONNECT_FAILED"
            else PALETTE["status_stale"]
            if self.state_key == "SIGNAL_LOST"
            else PALETTE["text_secondary"]
        )
        self.lbl_banner.config(text=banner_text, fg=banner_fg)

        busy = self.state_key in ("SCANNING", "CONNECTING")
        if self.state_key == "SCANNING":
            self.btn_scan.config(text="◎ 扫描中…", state=tk.DISABLED)
        else:
            self.btn_scan.config(text="↻ 重新扫描" if self.scanned_devices else "↻ 扫描设备",
                                 state=tk.DISABLED if busy or self.ble._client is not None else tk.NORMAL)

        self.btn_connect.config(state=tk.DISABLED if busy else tk.NORMAL)
        self.btn_disconnect.config(state=tk.NORMAL if self.ble._client is not None else tk.DISABLED)

        if self.state_key == "LIVE":
            self.btn_connect.config(text="✓ 已连接 · 重连", bg="#0D9488")
        elif self.state_key == "SIGNAL_LOST":
            self.btn_connect.config(text="↻ 重新连接设备", bg="#D97706")
        elif self.state_key == "CONNECT_FAILED":
            self.btn_connect.config(text="↻ 重试连接", bg="#E11D48")
        else:
            self.btn_connect.config(text="连接所选设备", bg="#0284C7")

        self._redraw_sparkline()

    # ------------------------------------------------------------------------
    # 绘制极简「红心 + 数字」画布 (支持圆角胶囊 PILL 与 纯透贴片 HUD)
    # ------------------------------------------------------------------------
    def _redraw_mini_heart_canvas(self) -> None:
        c = self.mini_canvas
        c.delete("all")
        is_spark = self.display_mode == "SPARK"
        w, h = (SPARK_PILL_WIDTH, SPARK_PILL_HEIGHT) if is_spark else (MINI_WIDTH, MINI_HEIGHT)
        is_hud = self.display_mode == "HUD"

        bg_clear = CHROMA_KEY_COLOR if self._supports_transparent_color else PALETTE["win_bg"]
        c.configure(bg=bg_clear)

        # 状态配色与角标定义
        if self.state_key == "LIVE" and self.current_bpm is not None:
            heart_fill = PALETTE["accent_heart"]
            heart_outline = "#FFE4E6"
            num_str = f"{self.current_bpm:>3d}"
            num_color = PALETTE["text_primary"]
            border_color = PALETTE["win_border"]
            badge = None
        elif self.state_key == "SIGNAL_LOST" and self.current_bpm is not None:
            heart_fill = PALETTE["status_stale"]
            heart_outline = "#FEF3C7"
            num_str = f"{self.current_bpm:>3d}"
            num_color = "#FBBF24"
            border_color = "#92400E"
            badge = ("!", "#451A03", "#F59E0B", "#FCD34D")
        elif self.state_key in ("SCANNING", "CONNECTING", "DEVICES_FOUND"):
            heart_fill = ""
            heart_outline = PALETTE["status_scan"]
            num_str = "···"
            num_color = PALETTE["status_scan"]
            border_color = "#0369A1"
            badge = ("◎", "#082F49", "#38BDF8", "#7DD3FC")
        elif self.state_key == "CONNECT_FAILED":
            heart_fill = ""
            heart_outline = PALETTE["status_error"]
            num_str = "ERR"
            num_color = "#FB7185"
            border_color = "#9F1239"
            badge = ("✕", "#4C0519", "#F43F5E", "#FDA4AF")
        else:
            heart_fill = ""
            heart_outline = PALETTE["text_muted"]
            num_str = " --"
            num_color = PALETTE["text_muted"]
            border_color = PALETTE["win_border"]
            badge = None

        # 1. 若为 PILL 胶囊模式，绘制圆角胶囊底板与 1.5px 描边
        if not is_hud:
            self._draw_rounded_pill_bg(c, 2, 2, w - 2, h - 2, radius=22, fill=PALETTE["win_bg"], outline=border_color)

        # 2. 绘制左侧矢量红心 (带实时脉动缩放)
        scale = 1.0
        if self.state_key == "LIVE":
            if self._heart_beat_phase == 1:
                scale = 1.15
            elif self._heart_beat_phase == 2:
                scale = 0.96
            elif self._heart_beat_phase == 3:
                scale = 1.07

        h_cx, h_cy = 26 if is_spark else 28, h // 2
        if is_hud:
            # HUD 纯透模式下先画一层深色投影心形保证浅底壁纸可读
            self._draw_vector_heart(c, h_cx + 1, h_cy + 2, 11.5 * scale, fill="#000000", outline="#000000")
        self._draw_vector_heart(
            c,
            h_cx,
            h_cy,
            (10.5 if is_spark else 11.0) * scale,
            fill=heart_fill,
            outline=heart_outline,
            width=2 if not heart_fill else 1,
        )

        # 3. 绘制状态微角标（断线 ! / 扫描 ◎ / 失败 ✕）
        if badge is not None:
            b_char, b_bg, b_bdr, b_fg = badge
            bx, by = h_cx + 9, h_cy + 8
            c.create_oval(bx - 6, by - 6, bx + 6, by + 6, fill=b_bg, outline=b_bdr, width=1)
            c.create_text(bx, by, text=b_char, fill=b_fg, font=("Consolas", 7, "bold"))

        # 4. 绘制右侧 3 字符等宽心率数字（右边界严格固定在 x = w - 16，2位数/3位数零位移）
        num_x = 114 if is_spark else w - 16
        num_y = h // 2
        font_spec = ("Consolas", 24, "bold")
        if is_hud:
            # HUD 模式下绘制 4 向深色轮廓描边，确保在纯白文档或深色代码上都锐利可读
            for ox, oy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, 2)):
                c.create_text(num_x + ox, num_y + oy, text=num_str, fill="#000000", font=font_spec, anchor="e")
        c.create_text(num_x, num_y, text=num_str, fill=num_color, font=font_spec, anchor="e", tags="mini_bpm")
        if is_spark:
            self._draw_mini_sparkline()

    def _draw_mini_sparkline(self) -> None:
        """Scheme C's 50×22 px trail, driven by actual BLE samples."""
        c = self.mini_canvas
        x0, y0, width, height = 128, 13, 50, 22
        samples = [bpm for _, bpm in list(self.hr_history)[-20:]]
        if len(samples) < 2 or self.state_key not in ("LIVE", "SIGNAL_LOST"):
            c.create_line(x0, y0 + height // 2, x0 + width, y0 + height // 2,
                          fill="#263040", width=1.5, dash=(3, 3), tags="mini_sparkline")
            return
        lo, hi = min(samples), max(samples)
        if hi - lo < 8:
            mid = (hi + lo) / 2.0
            lo, hi = mid - 4.0, mid + 4.0
        coords = []
        for index, bpm in enumerate(samples):
            coords.extend((x0 + index / (len(samples) - 1) * width,
                           y0 + height - (bpm - lo) / (hi - lo) * height))
        color = PALETTE["status_stale"] if self.state_key == "SIGNAL_LOST" else PALETTE["accent_hr"]
        c.create_line(coords, fill=color, width=2, smooth=False,
                      capstyle=tk.ROUND, joinstyle=tk.ROUND, tags="mini_sparkline")
        lx, ly = coords[-2:]
        c.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill=color,
                      outline="#FFFFFF", width=1.2, tags="mini_spark_endpoint")

    @staticmethod
    def _draw_rounded_pill_bg(
        canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, radius: int, fill: str, outline: str
    ) -> None:
        """在 Tkinter Canvas 上绘制平滑两端半圆的胶囊背景"""
        r = min(radius, (y2 - y1) // 2)
        # 填充部分
        canvas.create_oval(x1, y1, x1 + 2 * r, y2, fill=fill, outline=outline, width=2)
        canvas.create_oval(x2 - 2 * r, y1, x2, y2, fill=fill, outline=outline, width=2)
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
        # 上下描边线
        canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=2)
        canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=2)

    @staticmethod
    def _draw_vector_heart(
        canvas: tk.Canvas, cx: float, cy: float, radius: float, fill: str, outline: str, width: int = 1
    ) -> None:
        """使用参数化心形方程在 Tkinter Canvas 上绘制矢量红心"""
        coords: List[float] = []
        for deg in range(0, 360, 6):
            t = math.radians(deg)
            x = 16.0 * (math.sin(t) ** 3)
            y = -(13.0 * math.cos(t) - 5.0 * math.cos(2 * t) - 2.0 * math.cos(3 * t) - math.cos(4 * t))
            px = cx + x * (radius / 16.0)
            py = cy + y * (radius / 16.0)
            coords.extend([px, py])
        canvas.create_polygon(coords, fill=fill, outline=outline, width=width, smooth=True)

    # ------------------------------------------------------------------------
    # 绘制 60 秒实时心率曲线 (Full Mode)
    # ------------------------------------------------------------------------
    def _redraw_sparkline(self) -> None:
        c = self.plot_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w <= 10:
            w = FULL_WIDTH - 22
        if h <= 10:
            h = 72

        pad_left, pad_right, pad_top, pad_bottom = 28, 8, 8, 16
        plot_w = max(10, w - pad_left - pad_right)
        plot_h = max(10, h - pad_top - pad_bottom)

        for frac in (0.0, 0.5, 1.0):
            gy = pad_top + int(frac * plot_h)
            c.create_line(pad_left, gy, w - pad_right, gy, fill=PALETTE["plot_grid"], dash=(2, 2))

        for t_label, x_frac in (("-60s", 0.0), ("-30s", 0.5), ("现在", 1.0)):
            gx = pad_left + int(x_frac * plot_w)
            c.create_text(
                gx,
                h - 7,
                text=t_label,
                fill=PALETTE["text_muted"],
                font=("Segoe UI", 7),
                anchor="w" if x_frac == 0.0 else "e" if x_frac == 1.0 else "center",
            )

        now = time.time() if self.last_sample_ts is None else max(time.time(), self.last_sample_ts)
        if self.state_key == "SIGNAL_LOST" and self.last_sample_ts is not None:
            now = self.last_sample_ts + 8.0

        points_60s = [(t, b) for (t, b) in self.hr_history if now - 60.0 <= t <= now]
        if not points_60s:
            empty_hint = (
                "正在等待设备心率广播数据 (0x2A37)…"
                if self.state_key in ("SCANNING", "CONNECTING", "DEVICES_FOUND")
                else "最近 60 秒暂无心率曲线数据"
            )
            c.create_text(
                pad_left + plot_w // 2,
                pad_top + plot_h // 2,
                text=empty_hint,
                fill=PALETTE["text_muted"],
                font=("Segoe UI", 8),
            )
            return

        bpms = [b for (_, b) in points_60s]
        y_min = max(40, (min(bpms) - 6) // 5 * 5)
        y_max = min(230, ((max(bpms) + 6) // 5 + 1) * 5)
        if y_max - y_min < 20:
            mid = (y_max + y_min) // 2
            y_min, y_max = mid - 10, mid + 10

        c.create_text(pad_left - 4, pad_top + 3, text=str(y_max), fill=PALETTE["text_muted"], font=("Consolas", 7), anchor="e")
        c.create_text(pad_left - 4, pad_top + plot_h - 2, text=str(y_min), fill=PALETTE["text_muted"], font=("Consolas", 7), anchor="e")

        line_color = PALETTE["status_stale"] if self.state_key == "SIGNAL_LOST" else PALETTE["accent_hr"]

        coords: List[int] = []
        for t, b in points_60s:
            x = pad_left + int(((t - (now - 60.0)) / 60.0) * plot_w)
            y_ratio = (b - y_min) / float(max(1, y_max - y_min))
            y = pad_top + plot_h - int(y_ratio * plot_h)
            coords.extend([x, y])

        if len(coords) >= 4:
            poly_coords = [coords[0], pad_top + plot_h] + coords + [coords[-2], pad_top + plot_h]
            fill_tint = "#292012" if self.state_key == "SIGNAL_LOST" else "#0C2922"
            c.create_polygon(poly_coords, fill=fill_tint, outline="")
            c.create_line(coords, fill=line_color, width=2, smooth=True)

        last_x, last_y = coords[-2], coords[-1]
        c.create_oval(last_x - 3, last_y - 3, last_x + 3, last_y + 3, fill=line_color, outline="#FFFFFF", width=1)

    # ------------------------------------------------------------------------
    # 定时器：新鲜度检查、信号中断检测与红心搏动动画
    # ------------------------------------------------------------------------
    def _schedule_periodic_refresh(self) -> None:
        if self._closing:
            return
        self._periodic_tick()
        self.root.after(1000, self._schedule_periodic_refresh)

    def _periodic_tick(self) -> None:
        now = time.time()
        if self._demo_sim_active and self.state_key == "LIVE":
            base = 158 if (self.current_bpm or 80) >= 115 else 78
            jitter = int(round(4 * math.sin(now * 0.45) + random.choice([-1, 0, 1])))
            self._ingest_hr_sample(max(48, min(205, base + jitter)), now)
            return

        if self.state_key == "LIVE" and self.last_sample_ts is not None:
            if now - self.last_sample_ts > 5.5:
                self._set_state(
                    "SIGNAL_LOST",
                    "超过 5 秒未收到心率广播，保留最后数值；请确认设备仍在广播范围内。",
                )
                return

        if self.state_key in ("LIVE", "SIGNAL_LOST"):
            self._apply_state_visuals()

    def _schedule_heart_animation(self) -> None:
        if self._closing:
            return
        if self.state_key == "LIVE" and self.display_mode in MINI_MODES:
            self._heart_beat_phase = (self._heart_beat_phase + 1) % 6
            self._redraw_mini_heart_canvas()
        else:
            self._heart_beat_phase = 0
        # 约按当前心率节奏微脉动
        bpm = self.current_bpm if (self.current_bpm and 40 <= self.current_bpm <= 220) else 75
        interval_ms = max(90, int(60000 / bpm / 6))
        self.root.after(interval_ms, self._schedule_heart_animation)

    # ------------------------------------------------------------------------
    # 预设状态加载器 (支持一键验证 7 种状态与 2/3位数稳定性)
    # ------------------------------------------------------------------------
    def load_preset_state(self, preset_code: str, keep_current_mode: bool = True) -> None:
        if not self.demo_mode:
            return
        now = time.time()
        self._demo_sim_active = False

        sample_devices = [
            ScannedDeviceItem(address="D4:7A:E2:19:04:8B", name="BLE 心率传感器", rssi=-54, has_hr_service=True),
            ScannedDeviceItem(address="C1:09:4F:88:3A:12", name="BLE 心率胸带", rssi=-68, has_hr_service=True),
            ScannedDeviceItem(address="7E:33:1B:90:CC:41", name="", rssi=-82, has_hr_service=False),
        ]

        if preset_code == "DISCONNECTED":
            self.hr_history.clear()
            self.current_bpm = None
            self.last_sample_ts = None
            self.connected_device_name = "尚未选择"
            self._update_device_listbox([])
            self._set_state("DISCONNECTED", "开启设备的心率广播，点击「扫描设备」搜索附近设备")

        elif preset_code == "SCANNING":
            self.hr_history.clear()
            self.current_bpm = None
            self.last_sample_ts = None
            self.connected_device_name = "正在搜索…"
            self._update_device_listbox([])
            self._set_state("SCANNING", "正在扫描附近蓝牙低功耗设备，优先识别心率服务 0x180D…")

        elif preset_code == "DEVICES_FOUND":
            self.hr_history.clear()
            self.current_bpm = None
            self.last_sample_ts = None
            self.connected_device_name = "待连接 (BLE 心率传感器)"
            self._update_device_listbox(sample_devices)
            self._set_state("DEVICES_FOUND", "发现 3 台设备（已优先标记 [心率 0x180D]），请选择并点击连接")

        elif preset_code == "CONNECTING":
            self.hr_history.clear()
            self.current_bpm = None
            self.last_sample_ts = None
            self.connected_device_name = "BLE 心率传感器"
            self._update_device_listbox(sample_devices)
            self._set_state("CONNECTING", "正在与 BLE 心率传感器 握手并订阅心率通知特征 (0x2A37)…")

        elif preset_code in ("LIVE_2DIGIT", "LIVE_3DIGIT"):
            base_hr = 76 if preset_code == "LIVE_2DIGIT" else 162
            self.hr_history.clear()
            for i in range(60, -1, -1):
                t = now - i
                wave = int(round(6 * math.sin(i * 0.18) + (2 if i % 5 == 0 else -1)))
                self.hr_history.append((t, base_hr + wave))
            self.current_bpm = self.hr_history[-1][1]
            self.last_sample_ts = now
            self.connected_device_name = "BLE 心率传感器"
            self._update_device_listbox(sample_devices)
            self._demo_sim_active = True
            self._set_state("LIVE", "已连接 BLE 心率传感器 (0x180D)，可点击顶部「♥极简」缩为红心胶囊")

        elif preset_code == "SIGNAL_LOST":
            self.hr_history.clear()
            stale_ts = now - 14.0
            for i in range(60, 13, -1):
                t = now - i
                wave = int(round(5 * math.cos(i * 0.2)))
                self.hr_history.append((t, 142 + wave))
            self.current_bpm = 144
            self.last_sample_ts = stale_ts
            self.connected_device_name = "BLE 心率传感器"
            self._update_device_listbox(sample_devices)
            self._set_state("SIGNAL_LOST", "信号中断：已 14 秒未收到新心率包，保留最后数值 144 bpm")

        elif preset_code == "CONNECT_FAILED":
            self.hr_history.clear()
            self.current_bpm = None
            self.last_sample_ts = None
            self.connected_device_name = "BLE 心率传感器"
            self._update_device_listbox(sample_devices)
            self._set_state("CONNECT_FAILED", "连接超时或设备拒绝订阅 (0x2A37)：请确认设备支持 BLE 心率服务并已开启广播")

        if not keep_current_mode:
            self.set_display_mode("PILL" if preset_code in ("LIVE_2DIGIT", "LIVE_3DIGIT") else "FULL")
        else:
            self.set_display_mode(self.display_mode)


def main() -> None:
    demo = "--demo" in sys.argv
    configure_windows_app_id()
    root = tk.Tk()
    HeartRateMonitor(root, demo_mode=demo)
    root.mainloop()


if __name__ == "__main__":
    main()
