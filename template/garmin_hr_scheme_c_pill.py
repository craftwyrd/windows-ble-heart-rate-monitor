#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
方案 C · 极简「红心 + 数字 + 迷你心电尾迹」单行胶囊悬浮窗 (196 × 48 px)
纯粹、干净的 Tkinter 组件代码，方便直接复制或交给其他 AI 接入现有 BLE 逻辑。

布局规格 (196 × 48 px):
- 圆角胶囊背景: 深炭黑 (#11151C) + 1.5px 描边 (#2E3846)，Windows 下自动启用透明色键抠除四角
- 左侧 (x=26) : 参数化矢量红心 (#F43F5E)，支持随心跳微脉动 + 状态角标
- 中间 (x=114): 固定 3 字符等宽右对齐大数字 (Consolas 24pt Bold)，2位数与3位数绝对零跳动
- 右侧 (x=130..180): 50px 宽的极简近期心率尾迹折线 (#10B981) + 末端高亮发光白芯圆点 (无坐标轴与文字干扰)

对外极简接入接口:
- widget.update_hr(bpm: int)       -> 推入新收到的一笔心率值，自动刷新数字与右侧尾迹线
- widget.set_status(status: str)   -> 切换状态: "LIVE" | "STALE" (断线保留旧值) | "SCANNING" | "IDLE" | "ERROR"
"""

import collections
import math
import random
import tkinter as tk
from typing import Deque, List, Optional

# 尺寸与配色常量
PILL_WIDTH = 196
PILL_HEIGHT = 48
SPARK_WIDTH = 50
SPARK_HEIGHT = 22
CHROMA_KEY = "#010203"  # Windows 透明镂空色键

COLORS = {
    "pill_bg": "#11151C",
    "pill_border": "#2E3846",
    "heart_live": "#F43F5E",
    "heart_stale": "#F59E0B",
    "heart_scan": "#38BDF8",
    "heart_idle": "#64748B",
    "text_live": "#F8FAFC",
    "text_stale": "#FBBF24",
    "text_muted": "#64748B",
    "spark_live": "#10B981",
    "spark_stale": "#F59E0B",
    "spark_idle": "#263040",
}


class HeartSparkPillOverlay:
    """方案 C：红心 + 数字 + 迷你心电尾迹 (196×48 px) 独立悬浮窗"""

    def __init__(self, root: tk.Tk, max_points: int = 20):
        self.root = root
        self.status: str = "LIVE"
        self.current_bpm: Optional[int] = None
        self.history: Deque[int] = collections.deque(maxlen=max_points)

        self.is_topmost: bool = True
        self.opacity_levels: List[float] = [1.0, 0.88, 0.76]
        self.opacity_idx: int = 0
        self._beat_scale: float = 1.0
        self._drag_x: int = 0
        self._drag_y: int = 0
        self._supports_chroma: bool = False

        self._init_window()
        self._init_canvas()
        self._init_context_menu()
        self.redraw()

    # ------------------------------------------------------------------
    # 1. 窗口与画布初始化
    # ------------------------------------------------------------------
    def _init_window(self) -> None:
        self.root.title("Garmin HR Pill")
        self.root.overrideredirect(True)
        self.root.geometry(f"{PILL_WIDTH}x{PILL_HEIGHT}+180+180")
        try:
            self.root.wm_attributes("-topmost", self.is_topmost)
            self.root.wm_attributes("-alpha", self.opacity_levels[self.opacity_idx])
        except Exception:
            pass
        # Windows 下启用四角透明镂空，呈现真正圆润的独立胶囊
        try:
            self.root.wm_attributes("-transparentcolor", CHROMA_KEY)
            self._supports_chroma = True
        except Exception:
            self._supports_chroma = False

    def _init_canvas(self) -> None:
        bg = CHROMA_KEY if self._supports_chroma else COLORS["pill_bg"]
        self.root.configure(bg=bg)
        self.canvas = tk.Canvas(
            self.root,
            width=PILL_WIDTH,
            height=PILL_HEIGHT,
            bg=bg,
            bd=0,
            highlightthickness=0,
            cursor="fleur",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # 左键按住任意位置拖动，右键呼出极简菜单
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_motion)
        self.canvas.bind("<Button-3>", self._on_right_click)

    def _init_context_menu(self) -> None:
        self.menu = tk.Menu(
            self.root,
            tearoff=0,
            bg="#171C26",
            fg="#F8FAFC",
            activebackground="#0284C7",
            activeforeground="#FFFFFF",
            font=("Segoe UI", 9),
        )
        self.menu.add_command(label="📌 切换置顶", command=self.toggle_topmost)
        self.menu.add_command(label="◐ 切换透明度 (100%/88%/76%)", command=self.cycle_opacity)
        self.menu.add_separator()
        self.menu.add_command(label="✕ 关闭悬浮窗", command=self.root.destroy)

    # ------------------------------------------------------------------
    # 2. 对外数据更新接口 (直接对接 Bleak 0x2A37 回调即可)
    # ------------------------------------------------------------------
    def update_hr(self, bpm: int) -> None:
        """收到新的一笔心率数据时调用"""
        self.current_bpm = int(bpm)
        self.history.append(self.current_bpm)
        self.status = "LIVE"
        # 触发一次红心微搏动放大
        self._beat_scale = 1.16
        self.redraw()
        self.root.after(140, self._reset_beat_scale)

    def set_status(self, status: str) -> None:
        """
        设置状态:
        - "LIVE"    : 实时接收 (鲜红实心 + 白色数字 + 翠绿尾迹线)
        - "STALE"   : 信号中断 (琥珀黄心 + 保留最后数字与尾迹线)
        - "SCANNING": 扫描/连接中 (青蓝空心 + ···)
        - "IDLE"    : 未连接 (灰色空心 + --)
        - "ERROR"   : 连接失败 (红框空心 + ERR)
        """
        self.status = status.upper()
        self.redraw()

    def _reset_beat_scale(self) -> None:
        self._beat_scale = 1.0
        self.redraw()

    # ------------------------------------------------------------------
    # 3. 核心绘制逻辑：胶囊底板 + 左侧红心 + 中间定宽数字 + 右侧 48px 尾迹线
    # ------------------------------------------------------------------
    def redraw(self) -> None:
        c = self.canvas
        c.delete("all")
        w, h = PILL_WIDTH, PILL_HEIGHT

        # 状态配色映射
        if self.status == "LIVE" and self.current_bpm is not None:
            h_fill, h_out = COLORS["heart_live"], "#FFE4E6"
            num_txt, num_col = f"{self.current_bpm:>3d}", COLORS["text_live"]
            spark_col, bdr_col = COLORS["spark_live"], COLORS["pill_border"]
        elif self.status == "STALE" and self.current_bpm is not None:
            h_fill, h_out = COLORS["heart_stale"], "#FEF3C7"
            num_txt, num_col = f"{self.current_bpm:>3d}", COLORS["text_stale"]
            spark_col, bdr_col = COLORS["spark_stale"], "#92400E"
        elif self.status == "SCANNING":
            h_fill, h_out = "", COLORS["heart_scan"]
            num_txt, num_col = "···", COLORS["heart_scan"]
            spark_col, bdr_col = COLORS["spark_idle"], "#0369A1"
        elif self.status == "ERROR":
            h_fill, h_out = "", COLORS["heart_live"]
            num_txt, num_col = "ERR", "#FB7185"
            spark_col, bdr_col = COLORS["spark_idle"], "#9F1239"
        else:
            h_fill, h_out = "", COLORS["heart_idle"]
            num_txt, num_col = " --", COLORS["text_muted"]
            spark_col, bdr_col = COLORS["spark_idle"], COLORS["pill_border"]

        # (A) 绘制两端半圆的 196×48 胶囊背景与描边
        self._draw_pill_background(c, 2, 2, w - 2, h - 2, fill=COLORS["pill_bg"], outline=bdr_col)

        # (B) 绘制左侧红心 (中心点 x=26, y=24)
        self._draw_heart(
            c,
            cx=26,
            cy=h // 2,
            radius=10.5 * self._beat_scale,
            fill=h_fill,
            outline=h_out,
            width=2 if not h_fill else 1,
        )

        # (C) 绘制中间 3 字符等宽心率数字 (右边界固定锚定在 x=114，2位数/3位数零跳动)
        c.create_text(
            114,
            h // 2,
            text=num_txt,
            fill=num_col,
            font=("Consolas", 24, "bold"),
            anchor="e",
        )

        # (D) 绘制右侧 50px 极简心电尾迹线 (x: 128..178, y: 13..35)
        self._draw_mini_sparkline(c, x0=128, y0=13, width=SPARK_WIDTH, height=SPARK_HEIGHT, color=spark_col)

    @staticmethod
    def _draw_pill_background(canvas: tk.Canvas, x1: int, y1: int, x2: int, y2: int, fill: str, outline: str) -> None:
        r = (y2 - y1) // 2
        canvas.create_oval(x1, y1, x1 + 2 * r, y2, fill=fill, outline=outline, width=2)
        canvas.create_oval(x2 - 2 * r, y1, x2, y2, fill=fill, outline=outline, width=2)
        canvas.create_rectangle(x1 + r, y1, x2 - r, y2, fill=fill, outline="")
        canvas.create_line(x1 + r, y1, x2 - r, y1, fill=outline, width=2)
        canvas.create_line(x1 + r, y2, x2 - r, y2, fill=outline, width=2)

    @staticmethod
    def _draw_heart(canvas: tk.Canvas, cx: float, cy: float, radius: float, fill: str, outline: str, width: int = 1) -> None:
        pts: List[float] = []
        for deg in range(0, 360, 6):
            t = math.radians(deg)
            x = 16.0 * (math.sin(t) ** 3)
            y = -(13.0 * math.cos(t) - 5.0 * math.cos(2 * t) - 2.0 * math.cos(3 * t) - math.cos(4 * t))
            pts.extend([cx + x * (radius / 16.0), cy + y * (radius / 16.0)])
        canvas.create_polygon(pts, fill=fill, outline=outline, width=width, smooth=True)

    def _draw_mini_sparkline(self, canvas: tk.Canvas, x0: int, y0: int, width: int, height: int, color: str) -> None:
        pts_data = list(self.history)
        if len(pts_data) < 2 or self.status not in ("LIVE", "STALE"):
            # 无数据时仅画一条低调的水平基准虚线
            mid_y = y0 + height // 2
            canvas.create_line(x0, mid_y, x0 + width, mid_y, fill=COLORS["spark_idle"], width=1.5, dash=(3, 3))
            return

        lo, hi = min(pts_data), max(pts_data)
        if hi - lo < 8:
            mid = (hi + lo) / 2.0
            lo, hi = mid - 4.0, mid + 4.0

        coords: List[float] = []
        n = len(pts_data)
        for i, val in enumerate(pts_data):
            px = x0 + (i / float(n - 1)) * width
            py = y0 + height - ((val - lo) / float(hi - lo)) * height
            coords.extend([px, py])

        # 绘制平滑尾迹折线
        canvas.create_line(coords, fill=color, width=2, smooth=False, capstyle=tk.ROUND, joinstyle=tk.ROUND)

        # 绘制最右侧当前点的高亮白边圆点 (完全还原设计图右侧的小圆点)
        lx, ly = coords[-2], coords[-1]
        canvas.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill=color, outline="#FFFFFF", width=1.2)

    # ------------------------------------------------------------------
    # 4. 拖拽与窗口控制
    # ------------------------------------------------------------------
    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _on_drag_motion(self, event: tk.Event) -> None:
        nx = event.x_root - self._drag_x
        ny = event.y_root - self._drag_y
        self.root.geometry(f"{PILL_WIDTH}x{PILL_HEIGHT}+{nx}+{ny}")

    def _on_right_click(self, event: tk.Event) -> None:
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    def toggle_topmost(self) -> None:
        self.is_topmost = not self.is_topmost
        try:
            self.root.wm_attributes("-topmost", self.is_topmost)
        except Exception:
            pass

    def cycle_opacity(self) -> None:
        self.opacity_idx = (self.opacity_idx + 1) % len(self.opacity_levels)
        try:
            self.root.wm_attributes("-alpha", self.opacity_levels[self.opacity_idx])
        except Exception:
            pass


# ============================================================================
# 独立运行演示入口 (模拟实时心率推进，验证 138 bpm 与右侧尾迹线效果)
# ============================================================================
if __name__ == "__main__":
    root = tk.Tk()
    pill = HeartSparkPillOverlay(root, max_points=16)

    # 预填一组近期爬升心率数据以还原方案 C 视觉效果
    for sample in [124, 126, 125, 128, 127, 131, 130, 134, 133, 136, 138]:
        pill.update_hr(sample)

    def simulate_ble_stream() -> None:
        base = pill.current_bpm or 138
        next_bpm = max(60, min(195, base + random.choice([-2, -1, 0, 1, 2, 3])))
        pill.update_hr(next_bpm)
        root.after(1000, simulate_ble_stream)

    root.after(1000, simulate_ble_stream)
    root.mainloop()