#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Garmin Forerunner 265 实时心率桌面悬浮窗 (Windows Desktop BLE Receiver)
技术栈: Python 3 + Tkinter + Bleak (标准 BLE Heart Rate Service 0x180D / 0x2A37)

核心特性:
1. 三种显示形态无缝切换:
   - [PILL] 极简红心胶囊 (138×50 px): 仅显示左侧跳动红心 + 右侧 3 字符等宽大数字；
            平时零按钮干扰，鼠标悬停时顶部浮现微型控制条，双击或点击“展开”进入完整面板。
   - [HUD]  纯透无框贴片 (138×50 px): 利用 Windows -transparentcolor 抠除背景框，
            仅在桌面悬浮带深色阴影轮廓的红心与大数字。
   - [FULL] 完整曲线与连接管理窗 (316×336 px / 收起抽屉 316×176 px):
            包含 60 秒实时心率滚动曲线、均值/峰值统计、BLE 设备扫描与连接控制。
2. 零跳动排版约束:
   - 所有模式下心率数字均锁定 3 字符等宽右对齐槽位 (Consolas Bold)，2位数(76)与3位数(168)切换时窗口与红心零位移。
3. 多维状态视觉编码 (不仅靠颜色，结合心形实空/几何角标/文字胶囊):
   - 未连接 (○ 空心灰心 + --) / 扫描中 (◎ 空心蓝心 + ···) / 发现设备 (●) / 连接中 (◔)
   - 实时接收 (♥ 实心鲜红跳动心 + 白色数字)
   - 信号中断 (▲ 琥珀黄心 + ! 警告角标 + 保留最后有效心率值并标记过期秒数)
   - 连接失败 (✕ 红框空心 + X 角标 + 错误排查引导)

打包 Windows 单文件无黑框 EXE 命令:
    pip install bleak pyinstaller
    pyinstaller --noconsole --onefile --name "GarminHeartRateOverlay" garmin_hr_overlay.py
"""

import asyncio
import collections
import math
import random
import struct
import sys
import threading
import time
import tkinter as tk
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

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
        "banner": "在手表开启「广播心率」，点击下方「扫描设备」搜索手表",
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
        "banner": "已置顶广播 [心率 0x180D] 的设备，请选择手表并点击连接",
    },
    "CONNECTING": {
        "glyph": "◔",
        "label": "连接中…",
        "color": PALETTE["status_scan"],
        "badge_bg": "#122B3D",
        "banner": "正在与手表握手并订阅标准心率通知特征 (0x2A37)…",
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
        if len(base_name) > 19:
            base_name = base_name[:18] + "…"
        tag = "[心率 0x180D]" if self.has_hr_service else f"[{self.address_suffix}]"
        return f"{base_name:<19} {tag}"


def parse_ble_heart_rate(data: bytearray) -> Optional[int]:
    """
    解析 Bluetooth SIG 标准 Heart Rate Measurement (0x2A37) 字节流：
    - Byte 0 Flags 的 Bit 0: 0 表示 UINT8 (Byte 1)，1 表示 UINT16 Little-Endian (Byte 1..2)
    """
    if not data or len(data) < 2:
        return None
    flags = data[0]
    if flags & 0x01:
        if len(data) < 3:
            return None
        return int(struct.unpack_from("<H", data, 1)[0])
    return int(data[1])


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
        self._stop_requested = False

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="BleAsyncWorker")
        self._thread.start()

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

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

    async def _async_scan(self, timeout: float) -> None:
        if self._scanning:
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
                has_hr = HR_SERVICE_UUID in uuids or any("180d" in u for u in uuids)
                dev_name = dev.name or adv.local_name or ""
                if not has_hr and ("forerunner" in dev_name.lower() or "garmin" in dev_name.lower()):
                    has_hr = True
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
                    "未扫描到设备。请确认手表已开启「广播心率」且电脑蓝牙已打开。",
                )
        except Exception as exc:
            self.on_state_change("CONNECT_FAILED", f"蓝牙扫描异常：{str(exc)[:40]}")
        finally:
            self._scanning = False

    async def _async_connect(self, item: ScannedDeviceItem) -> None:
        if not BLEAK_AVAILABLE:
            self.on_state_change("CONNECT_FAILED", "当前环境未安装 bleak 库，无法发起真实 BLE 连接。")
            return

        await self._async_disconnect(silent=True)
        self.on_state_change("CONNECTING", f"正在连接 {item.name or item.address_suffix}…")

        def handle_disconnect(_client: BleakClient) -> None:
            if not self._stop_requested:
                self.on_state_change(
                    "SIGNAL_LOST",
                    "蓝牙连接已断开，保留最后心率值；可点击「重新连接」恢复。",
                )

        try:
            target = item.raw_device if item.raw_device is not None else item.address
            client = BleakClient(target, disconnected_callback=handle_disconnect, timeout=10.0)
            await client.connect()
            self._client = client

            def notification_handler(_characteristic, data: bytearray) -> None:
                bpm = parse_ble_heart_rate(data)
                if bpm is not None and 25 <= bpm <= 250:
                    self.on_hr_sample(bpm, time.time())

            try:
                await client.start_notify(HR_MEASUREMENT_UUID, notification_handler)
            except Exception:
                await client.disconnect()
                self._client = None
                self.on_state_change(
                    "CONNECT_FAILED",
                    "该设备未提供标准心率通知 (0x2A37)，请在手表开启「广播心率」。",
                )
                return

            self.on_state_change("LIVE", f"已连接 {item.name or item.address_suffix}")
        except asyncio.TimeoutError:
            self.on_state_change("CONNECT_FAILED", "连接超时：请确认手表处于广播心率页面且未被其他设备占用。")
        except Exception as exc:
            self.on_state_change("CONNECT_FAILED", f"连接失败 ({type(exc).__name__})，请靠近电脑后重试。")

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

class GarminHeartRateOverlayApp:
    """
    支持三种显示模式 (`display_mode`):
      - "PILL": 极简圆角红心胶囊 (138×50px)，左侧跳动红心 + 右侧 3字符等宽数字，悬停顶部浮现微工具栏
      - "HUD" : 纯透无框贴片 (138×50px)，在 Windows 下利用 -transparentcolor 去底框，仅保留红心与带阴影数字
      - "FULL": 完整窗口 (316×336px 展开连接区 / 316×176px 仅保留主数字与 60s 曲线)
    """

    def __init__(self, root: tk.Tk, demo_mode: bool = False):
        self.root = root
        self.demo_mode = demo_mode

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

        if self.demo_mode:
            self.load_preset_state("LIVE_2DIGIT", keep_current_mode=True)
        else:
            self.set_display_mode("FULL")
            self._apply_state_visuals()

        self._schedule_periodic_refresh()
        self._schedule_heart_animation()

    # ------------------------------------------------------------------------
    # 窗口底层属性设置
    # ------------------------------------------------------------------------
    def _configure_window(self) -> None:
        self.root.title("Garmin Heart Rate Overlay")
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

    def _sync_window_geometry(self, x: Optional[int] = None, y: Optional[int] = None) -> None:
        if self.display_mode in ("PILL", "HUD"):
            w = MINI_WIDTH
            h = MINI_HEIGHT + (MINI_HOVER_BAR_HEIGHT if self._mini_hover_active else 0)
        else:
            w = FULL_WIDTH
            h = FULL_EXPANDED_HEIGHT if self.drawer_expanded else FULL_COLLAPSED_HEIGHT

        if x is None or y is None:
            cur_geom = self.root.geometry()
            if "+" in cur_geom:
                parts = cur_geom.split("+")
                x, y = int(parts[1]), int(parts[2])
            else:
                x, y = 140, 140
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ------------------------------------------------------------------------
    # 构建极简「红心 + 数字」视图 (Mini Mode: PILL & HUD)
    # ------------------------------------------------------------------------
    def _build_mini_mode_ui(self) -> None:
        bg_col = CHROMA_KEY_COLOR if self._supports_transparent_color else PALETTE["win_bg"]
        self.mini_container = tk.Frame(self.root, bg=bg_col, bd=0, highlightthickness=0)

        # 1. 鼠标移入时才显示的顶部微型工具条 (24px 高)
        self.mini_hover_bar = tk.Frame(
            self.mini_container,
            bg="#0F172A",
            height=MINI_HOVER_BAR_HEIGHT,
            highlightbackground="#334155",
            highlightthickness=1,
        )
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
        if self.display_mode not in ("PILL", "HUD"):
            return
        if not self._mini_hover_active:
            self._mini_hover_active = True
            self.mini_hover_bar.pack(fill=tk.X, side=tk.TOP, pady=(0, 1))
            self.mini_canvas.pack(fill=tk.BOTH, expand=True)
            self._sync_window_geometry()

    def _on_mini_mouse_leave(self, event: tk.Event) -> None:
        if self.display_mode not in ("PILL", "HUD"):
            return
        # 检查鼠标是否真的离开整个 mini_container 矩形范围
        x, y = self.root.winfo_pointerxy()
        wx, wy = self.root.winfo_rootx(), self.root.winfo_rooty()
        ww, wh = self.root.winfo_width(), self.root.winfo_height()
        if wx <= x <= wx + ww and wy <= y <= wy + wh:
            return
        if self._mini_hover_active:
            self._mini_hover_active = False
            self.mini_hover_bar.pack_forget()
            self._sync_window_geometry()

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
            text="♥ GARMIN",
            bg=PALETTE["header_bg"],
            fg=PALETTE["text_secondary"],
            font=("Segoe UI", 8, "bold"),
            anchor="w",
            padx=8,
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
            padx=6,
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
            padx=4,
            cursor="hand2",
        )
        self.btn_drawer.pack(side=tk.RIGHT, fill=tk.Y)

        self.btn_pin = tk.Button(
            self.header_bar,
            text="📌置顶",
            command=self.toggle_topmost,
            bg=PALETTE["header_bar"] if "header_bar" in PALETTE else PALETTE["header_bg"],
            fg=PALETTE["text_primary"],
            activebackground=PALETTE["control_hover"],
            activeforeground=PALETTE["text_primary"],
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8),
            padx=4,
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
            padx=4,
            cursor="hand2",
        )
        self.btn_opacity.pack(side=tk.RIGHT, fill=tk.Y)

        # 一键切换为「♥极简红心胶囊」按钮
        self.btn_to_mini = tk.Button(
            self.header_bar,
            text="♥极简",
            command=lambda: self.set_display_mode("PILL"),
            bg=PALETTE["header_bg"],
            fg=PALETTE["accent_heart"],
            activebackground=PALETTE["control_hover"],
            activeforeground="#FFFFFF",
            bd=0,
            relief=tk.FLAT,
            font=("Segoe UI", 8, "bold"),
            padx=5,
            cursor="hand2",
        )
        self.btn_to_mini.pack(side=tk.RIGHT, fill=tk.Y)

        # ---- B. 核心数据主视图 (固定高度 64px，3字符槽位防止2/3位数跳动) ----
        self.hero_frame = tk.Frame(self.full_container, bg=PALETTE["win_bg"], height=64)
        self.hero_frame.pack(fill=tk.X, padx=10, pady=(4, 2))
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
            height=72,
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
            text="连接所选手表",
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

        # 底部状态快速验收预览栏
        sim_bar = tk.Frame(self.drawer_frame, bg=PALETTE["panel_bg"])
        sim_bar.pack(fill=tk.X, padx=6, pady=(0, 4))

        tk.Label(
            sim_bar,
            text="状态预览:",
            bg=PALETTE["panel_bg"],
            fg=PALETTE["text_muted"],
            font=("Segoe UI", 7),
        ).pack(side=tk.LEFT, padx=(0, 2))

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
        for title, code in presets:
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
            ).pack(side=tk.LEFT, padx=1)

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
        self.display_mode = mode
        if mode in ("PILL", "HUD"):
            self.full_container.pack_forget()
            self.mini_container.pack(fill=tk.BOTH, expand=True)
            self.btn_mini_hud.config(
                text="胶囊" if mode == "HUD" else "纯透",
                fg=PALETTE["accent_primary"] if mode == "HUD" else PALETTE["text_secondary"],
            )
        else:
            self._mini_hover_active = False
            self.mini_hover_bar.pack_forget()
            self.mini_container.pack_forget()
            self.full_container.pack(fill=tk.BOTH, expand=True)
        self._sync_window_geometry()
        self._apply_state_visuals()

    def toggle_pill_or_hud(self) -> None:
        next_mode = "HUD" if self.display_mode == "PILL" else "PILL"
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
        except Exception:
            pass
        self.btn_pin.config(
            text="📌置顶" if self.is_topmost else "📍普通",
            fg=PALETTE["text_primary"] if self.is_topmost else PALETTE["text_muted"],
        )

    def cycle_opacity(self) -> None:
        self.opacity_idx = (self.opacity_idx + 1) % len(self.opacity_levels)
        alpha = self.opacity_levels[self.opacity_idx]
        try:
            self.root.wm_attributes("-alpha", alpha)
        except Exception:
            pass
        pct_text = f"{int(alpha * 100)}%"
        self.btn_opacity.config(text=pct_text)
        self.btn_mini_opacity.config(text=pct_text)

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
        self._demo_sim_active = False
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
        self._demo_sim_active = False
        self.ble.trigger_scan(timeout=4.5)

    def on_click_connect(self) -> None:
        self._demo_sim_active = False
        if not self.scanned_devices:
            self._set_state("CONNECT_FAILED", "请先点击「扫描设备」并选择 Garmin 手表。")
            return
        idx = self.selected_device_index if 0 <= self.selected_device_index < len(self.scanned_devices) else 0
        target = self.scanned_devices[idx]
        self.connected_device_name = target.name or f"BLE {target.address_suffix}"
        self.ble.trigger_connect(target)

    def on_click_disconnect(self) -> None:
        self._demo_sim_active = False
        self.ble.trigger_disconnect()

    def _threadsafe_state_change(self, new_state: str, banner_msg: str) -> None:
        self.root.after(0, lambda: self._set_state(new_state, banner_msg))

    def _threadsafe_devices_updated(self, devices: List[ScannedDeviceItem]) -> None:
        self.root.after(0, lambda: self._update_device_listbox(devices))

    def _threadsafe_hr_sample(self, bpm: int, ts: float) -> None:
        self.root.after(0, lambda: self._ingest_hr_sample(bpm, ts))

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
            self.set_display_mode("PILL")
            return

        self._apply_state_visuals()

    def _update_device_listbox(self, devices: List[ScannedDeviceItem]) -> None:
        self.scanned_devices = devices
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

        self.lbl_device_caption.config(text=f"设备: {self.connected_device_name}")

        banner_text = self.banner_message or cfg["banner"] or "已就绪"
        banner_fg = (
            PALETTE["status_error"]
            if self.state_key == "CONNECT_FAILED"
            else PALETTE["status_stale"]
            if self.state_key == "SIGNAL_LOST"
            else PALETTE["text_secondary"]
        )
        self.lbl_banner.config(text=banner_text, fg=banner_fg)

        if self.state_key == "SCANNING":
            self.btn_scan.config(text="◎ 扫描中…", state=tk.DISABLED)
        else:
            self.btn_scan.config(text="↻ 重新扫描" if self.scanned_devices else "↻ 扫描设备", state=tk.NORMAL)

        if self.state_key == "LIVE":
            self.btn_connect.config(text="✓ 已连接 (点击重连)", bg="#0D9488")
        elif self.state_key == "SIGNAL_LOST":
            self.btn_connect.config(text="↻ 重新连接手表", bg="#D97706")
        elif self.state_key == "CONNECT_FAILED":
            self.btn_connect.config(text="↻ 重试连接", bg="#E11D48")
        else:
            self.btn_connect.config(text="连接所选手表", bg="#0284C7")

        self._redraw_sparkline()

    # ------------------------------------------------------------------------
    # 绘制极简「红心 + 数字」画布 (支持圆角胶囊 PILL 与 纯透贴片 HUD)
    # ------------------------------------------------------------------------
    def _redraw_mini_heart_canvas(self) -> None:
        c = self.mini_canvas
        c.delete("all")
        w, h = MINI_WIDTH, MINI_HEIGHT
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

        h_cx, h_cy = 28, h // 2
        if is_hud:
            # HUD 纯透模式下先画一层深色投影心形保证浅底壁纸可读
            self._draw_vector_heart(c, h_cx + 1, h_cy + 2, 11.5 * scale, fill="#000000", outline="#000000")
        self._draw_vector_heart(
            c,
            h_cx,
            h_cy,
            11.0 * scale,
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
        num_x = w - 16
        num_y = h // 2
        font_spec = ("Consolas", 24, "bold")
        if is_hud:
            # HUD 模式下绘制 4 向深色轮廓描边，确保在纯白文档或深色代码上都锐利可读
            for ox, oy in ((-1, -1), (1, -1), (-1, 1), (1, 1), (0, 2)):
                c.create_text(num_x + ox, num_y + oy, text=num_str, fill="#000000", font=font_spec, anchor="e")
        c.create_text(num_x, num_y, text=num_str, fill=num_color, font=font_spec, anchor="e")

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
                "正在等待手表心率广播数据 (0x2A37)…"
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
                    "超过 5 秒未收到心率广播，保留最后数值；请确认手表仍在广播范围内。",
                )
                return

        if self.state_key in ("LIVE", "SIGNAL_LOST"):
            self._apply_state_visuals()

    def _schedule_heart_animation(self) -> None:
        if self.state_key == "LIVE" and self.display_mode in ("PILL", "HUD"):
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
        now = time.time()
        self._demo_sim_active = False

        sample_devices = [
            ScannedDeviceItem(address="D4:7A:E2:19:04:8B", name="Forerunner 265", rssi=-54, has_hr_service=True),
            ScannedDeviceItem(address="C1:09:4F:88:3A:12", name="HRM-Pro Plus", rssi=-68, has_hr_service=True),
            ScannedDeviceItem(address="7E:33:1B:90:CC:41", name="", rssi=-82, has_hr_service=False),
        ]

        if preset_code == "DISCONNECTED":
            self.hr_history.clear()
            self.current_bpm = None
            self.last_sample_ts = None
            self.connected_device_name = "尚未选择"
            self._update_device_listbox([])
            self._set_state("DISCONNECTED", "在手表开启「广播心率」，点击下方「扫描设备」搜索手表")

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
            self.connected_device_name = "待连接 (Forerunner 265)"
            self._update_device_listbox(sample_devices)
            self._set_state("DEVICES_FOUND", "发现 3 台设备（已优先标记 [心率 0x180D]），请选择并点击连接")

        elif preset_code == "CONNECTING":
            self.hr_history.clear()
            self.current_bpm = None
            self.last_sample_ts = None
            self.connected_device_name = "Forerunner 265"
            self._update_device_listbox(sample_devices)
            self._set_state("CONNECTING", "正在与 Forerunner 265 握手并订阅心率通知特征 (0x2A37)…")

        elif preset_code in ("LIVE_2DIGIT", "LIVE_3DIGIT"):
            base_hr = 76 if preset_code == "LIVE_2DIGIT" else 162
            self.hr_history.clear()
            for i in range(60, -1, -1):
                t = now - i
                wave = int(round(6 * math.sin(i * 0.18) + (2 if i % 5 == 0 else -1)))
                self.hr_history.append((t, base_hr + wave))
            self.current_bpm = self.hr_history[-1][1]
            self.last_sample_ts = now
            self.connected_device_name = "Forerunner 265"
            self._update_device_listbox(sample_devices)
            self._demo_sim_active = True
            self._set_state("LIVE", "已连接 Forerunner 265 (0x180D)，可点击顶部「♥极简」缩为红心胶囊")

        elif preset_code == "SIGNAL_LOST":
            self.hr_history.clear()
            stale_ts = now - 14.0
            for i in range(60, 13, -1):
                t = now - i
                wave = int(round(5 * math.cos(i * 0.2)))
                self.hr_history.append((t, 142 + wave))
            self.current_bpm = 144
            self.last_sample_ts = stale_ts
            self.connected_device_name = "Forerunner 265"
            self._update_device_listbox(sample_devices)
            self._set_state("SIGNAL_LOST", "信号中断：已 14 秒未收到新心率包，保留最后数值 144 bpm")

        elif preset_code == "CONNECT_FAILED":
            self.hr_history.clear()
            self.current_bpm = None
            self.last_sample_ts = None
            self.connected_device_name = "Forerunner 265"
            self._update_device_listbox(sample_devices)
            self._set_state("CONNECT_FAILED", "连接超时或设备拒绝订阅 (0x2A37)：请确认手表处于心率广播页面")

        if not keep_current_mode:
            self.set_display_mode("PILL" if preset_code in ("LIVE_2DIGIT", "LIVE_3DIGIT") else "FULL")
        else:
            self.set_display_mode(self.display_mode)


def main() -> None:
    demo = "--demo" in sys.argv or not BLEAK_AVAILABLE
    root = tk.Tk()
    GarminHeartRateOverlayApp(root, demo_mode=demo)
    root.mainloop()


if __name__ == "__main__":
    main()