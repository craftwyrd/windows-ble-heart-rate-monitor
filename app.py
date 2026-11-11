from __future__ import annotations

import asyncio
import threading
import time
import tkinter as tk
from collections import deque
from concurrent.futures import Future
from tkinter import ttk
from typing import Callable

from bleak import BleakClient, BleakScanner
from bleak.backends.device import BLEDevice

HEART_RATE_SERVICE = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


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


class BleWorker:
    def __init__(self, on_status: Callable[[str], None], on_devices: Callable, on_bpm: Callable):
        self.on_status = on_status
        self.on_devices = on_devices
        self.on_bpm = on_bpm
        self.loop = asyncio.new_event_loop()
        self.client: BleakClient | None = None
        self.devices: dict[str, BLEDevice] = {}
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coroutine) -> Future:
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop)

    async def scan(self) -> None:
        self.on_status("正在扫描附近的蓝牙设备…")
        try:
            found = await BleakScanner.discover(timeout=7.0, return_adv=True)
            self.devices.clear()
            rows = []
            for address, (device, advertisement) in found.items():
                name = advertisement.local_name or device.name or "未命名设备"
                services = {item.lower() for item in (advertisement.service_uuids or [])}
                has_hr = HEART_RATE_SERVICE in services
                label = f"{name}  ·  {address}" + ("  ·  心率服务" if has_hr else "")
                self.devices[label] = device
                rows.append((label, has_hr))
            rows.sort(key=lambda row: (not row[1], row[0].casefold()))
            self.on_devices([label for label, _ in rows])
            if rows:
                self.on_status(f"扫描完成，发现 {len(rows)} 个设备。带有“心率服务”的设备优先尝试连接。")
            else:
                self.on_status("没有发现蓝牙设备。请确认电脑蓝牙已开启，并让手表保持在心率广播页面。")
        except Exception as exc:
            self.on_status(f"扫描失败：{exc}")

    async def connect(self, label: str) -> None:
        device = self.devices.get(label)
        if not device:
            self.on_status("请先扫描并选择一个设备。")
            return
        await self.disconnect(update_status=False)
        self.on_status(f"正在连接 {device.name or device.address}…")
        try:
            client = BleakClient(device, timeout=15.0, disconnected_callback=self._disconnected)
            await client.connect()
            characteristic = next(
                (
                    char
                    for service in client.services
                    for char in service.characteristics
                    if char.uuid.lower() == HEART_RATE_MEASUREMENT
                    and "notify" in char.properties
                ),
                None,
            )
            if characteristic is None:
                await client.disconnect()
                self.on_status(
                    "设备已连接，但没有标准 BLE 心率通知服务。请确认手表开启的是心率广播功能。"
                )
                return

            self.client = client
            await client.start_notify(characteristic, self._notification)
            self.on_status(f"已连接 {device.name or device.address}，等待心率数据…")
        except Exception as exc:
            if "client" in locals() and client.is_connected:
                try:
                    await client.disconnect()
                except Exception:
                    pass
            self.client = None
            self.on_status(f"连接失败：{exc}")

    async def disconnect(self, update_status: bool = True) -> None:
        client, self.client = self.client, None
        if client and client.is_connected:
            try:
                await client.disconnect()
            except Exception:
                pass
        if update_status:
            self.on_status("已断开连接。")

    def _notification(self, _sender, data: bytearray) -> None:
        try:
            bpm = parse_heart_rate(data)
        except ValueError as exc:
            self.on_status(str(exc))
            return
        self.on_bpm(bpm, time.time())

    def _disconnected(self, _client: BleakClient) -> None:
        self.client = None
        self.on_status("蓝牙连接已断开。")


class HeartRateMonitor:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Garmin 心率")
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", 0.94)
        self.root.attributes("-topmost", True)
        left = max(8, self.root.winfo_screenwidth() - 372)
        self.root.geometry(f"352x360+{left}+42")
        self.root.configure(bg="#101916")
        self.root.resizable(False, False)
        self.history: deque[tuple[float, int]] = deque(maxlen=240)
        self.always_on_top = True
        self.controls_open = True
        self.drag_origin: tuple[int, int, int, int] | None = None
        self.last_bpm_at: float | None = None
        self._closing = False
        self._build_ui()
        self.worker = BleWorker(
            self._set_status,
            lambda values: self.root.after(0, self._set_devices, values),
            lambda bpm, timestamp: self.root.after(0, self._set_bpm, bpm, timestamp),
        )
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.after(1000, self._refresh_signal_state)

    def _build_ui(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Dark.TCombobox",
            padding=(8, 7),
            foreground="#e4eee8",
            fieldbackground="#1d2924",
            background="#26352e",
            arrowcolor="#b5c7bc",
            bordercolor="#35463d",
            lightcolor="#35463d",
            darkcolor="#35463d",
        )
        style.map(
            "Dark.TCombobox",
            fieldbackground=[("readonly", "#1d2924")],
            foreground=[("readonly", "#e4eee8")],
            selectbackground=[("readonly", "#26352e")],
            selectforeground=[("readonly", "#e4eee8")],
        )
        style.configure(
            "TButton",
            background="#26352e",
            foreground="#dce9e0",
            bordercolor="#405147",
            lightcolor="#405147",
            darkcolor="#405147",
            padding=(9, 7),
            font=("Segoe UI", 9),
        )
        style.map(
            "TButton",
            background=[("active", "#34483d"), ("disabled", "#202a25")],
            foreground=[("disabled", "#748177")],
        )
        style.configure(
            "Connect.TButton",
            background="#286447",
            foreground="#f0fff5",
            bordercolor="#3b8b61",
            padding=(10, 7),
            font=("Segoe UI", 9, "bold"),
        )
        style.map("Connect.TButton", background=[("active", "#347a55"), ("disabled", "#26352e")])
        style.configure("Scan.TButton", padding=(9, 7), font=("Segoe UI", 9))

        shell = tk.Frame(
            self.root,
            bg="#121b17",
            highlightbackground="#42554a",
            highlightcolor="#42554a",
            highlightthickness=1,
            padx=14,
            pady=12,
        )
        shell.pack(fill="both", expand=True, padx=1, pady=1)

        header = tk.Frame(shell, bg="#121b17")
        header.pack(fill="x")
        self.state_dot = tk.Label(header, text="●", font=("Segoe UI", 8), fg="#75867b", bg="#121b17")
        self.state_dot.pack(side="left", padx=(0, 6))
        self.state_label = tk.Label(header, text="GARMIN · WAITING", font=("Segoe UI", 9, "bold"), fg="#c2d2c7", bg="#121b17")
        self.state_label.pack(side="left")
        for label, command, tip in (
            ("SETUP", self.toggle_controls, "显示或隐藏设备连接选项"),
            ("⌖", self.toggle_topmost, "切换窗口置顶"),
            ("−", self.toggle_compact, "收起或展开悬浮窗"),
            ("×", self.close, "关闭心率接收器"),
        ):
            button = tk.Button(
                header,
                text=label,
                command=command,
                font=("Segoe UI Symbol", 9, "bold"),
                fg="#aabbb0",
                activeforeground="#ffffff",
                bg="#121b17",
                activebackground="#26352e",
                relief="flat",
                bd=0,
                padx=6,
                pady=2,
                cursor="hand2",
                takefocus=True,
            )
            button.pack(side="right", padx=(2, 0))
            self._attach_tooltip(button, tip)

        self.drag_handle = tk.Label(
            header,
            text="",
            bg="#121b17",
            width=2,
            cursor="fleur",
        )
        self.drag_handle.pack(side="right", fill="x", expand=True)
        self.drag_handle.bind("<ButtonPress-1>", self._start_drag)
        self.drag_handle.bind("<B1-Motion>", self._drag)
        self.state_label.bind("<ButtonPress-1>", self._start_drag)
        self.state_label.bind("<B1-Motion>", self._drag)

        body = tk.Frame(shell, bg="#121b17")
        body.pack(fill="both", expand=True, pady=(7, 0))
        top = tk.Frame(body, bg="#121b17")
        top.pack(fill="x")
        number_group = tk.Frame(top, bg="#121b17")
        number_group.pack(side="left", anchor="s")
        self.bpm_label = tk.Label(number_group, text="—", font=("Segoe UI", 43, "bold"), fg="#e5f1e9", bg="#121b17", width=3, anchor="e")
        self.bpm_label.pack(side="left")
        tk.Label(number_group, text="bpm", font=("Segoe UI", 11, "bold"), fg="#94aa9c", bg="#121b17").pack(side="left", anchor="s", pady=(0, 11))

        stats = tk.Frame(top, bg="#121b17")
        stats.pack(side="right", anchor="s", pady=(0, 9))
        self.average_label = tk.Label(stats, text="AVG  --", font=("Segoe UI", 9, "bold"), fg="#9ab7a5", bg="#121b17", anchor="e")
        self.average_label.pack(anchor="e")
        self.high_label = tk.Label(stats, text="HIGH  —", font=("Segoe UI", 9, "bold"), fg="#e3a58e", bg="#121b17", anchor="e")
        self.high_label.pack(anchor="e", pady=(3, 0))

        chart_head = tk.Frame(body, bg="#121b17")
        chart_head.pack(fill="x", pady=(1, 3))
        tk.Label(chart_head, text="最近 60 秒", font=("Segoe UI", 8, "bold"), fg="#9db0a3", bg="#121b17").pack(side="left")
        self.last_update = tk.Label(chart_head, text="等待数据", font=("Segoe UI", 8), fg="#829488", bg="#121b17")
        self.last_update.pack(side="right")
        self.chart = tk.Canvas(body, height=72, bg="#121b17", highlightthickness=0, bd=0)
        self.chart.pack(fill="x")
        self.chart.bind("<Configure>", lambda _event: self._draw_chart())

        self.status = tk.StringVar(value="打开手表心率广播后，扫描并连接。")
        self.status_label = tk.Label(
            body,
            textvariable=self.status,
            anchor="w",
            justify="left",
            wraplength=318,
            font=("Segoe UI", 8),
            fg="#9fb0a5",
            bg="#121b17",
        )
        self.status_label.pack(fill="x", pady=(2, 0))

        self.controls = tk.Frame(shell, bg="#121b17")
        tk.Frame(self.controls, height=1, bg="#2a3931").pack(fill="x", pady=(9, 8))
        device_row = tk.Frame(self.controls, bg="#121b17")
        device_row.pack(fill="x")
        self.device_box = ttk.Combobox(device_row, state="readonly", values=[], style="Dark.TCombobox")
        self.device_box.pack(side="left", fill="x", expand=True)
        self.scan_button = ttk.Button(device_row, text="扫描", style="Scan.TButton", command=self.scan)
        self.scan_button.pack(side="left", padx=(7, 0))
        action_row = tk.Frame(self.controls, bg="#121b17")
        action_row.pack(fill="x", pady=(7, 0))
        self.connect_button = ttk.Button(action_row, text="连接手表", style="Connect.TButton", command=self.connect)
        self.connect_button.pack(side="left")
        self.disconnect_button = ttk.Button(action_row, text="断开", style="Scan.TButton", command=self.disconnect, state="disabled")
        self.disconnect_button.pack(side="left", padx=(7, 0))
        tk.Label(action_row, text="BLE 心率", font=("Segoe UI", 8), fg="#71877a", bg="#121b17").pack(side="right", pady=(4, 0))
        self.controls.pack(fill="x", pady=(0, 0))

        self._draw_chart()

    def _attach_tooltip(self, widget: tk.Widget, text: str) -> None:
        popup: tk.Toplevel | None = None

        def show(_event) -> None:
            nonlocal popup
            if popup is not None:
                return
            popup = tk.Toplevel(self.root)
            popup.overrideredirect(True)
            popup.attributes("-topmost", self.always_on_top)
            label = tk.Label(popup, text=text, bg="#26352e", fg="#edf5ef", padx=8, pady=5, font=("Segoe UI", 8))
            label.pack()
            popup.geometry(f"+{widget.winfo_rootx()}+{widget.winfo_rooty() + widget.winfo_height() + 3}")

        def hide(_event) -> None:
            nonlocal popup
            if popup is not None:
                popup.destroy()
                popup = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")

    def _start_drag(self, event) -> None:
        self.drag_origin = (event.x_root, event.y_root, self.root.winfo_x(), self.root.winfo_y())

    def _drag(self, event) -> None:
        if self.drag_origin is None:
            return
        x0, y0, left, top = self.drag_origin
        self.root.geometry(f"+{left + event.x_root - x0}+{top + event.y_root - y0}")

    def toggle_topmost(self) -> None:
        self.always_on_top = not self.always_on_top
        self.root.attributes("-topmost", self.always_on_top)
        self.status.set("窗口已置顶。" if self.always_on_top else "窗口不再置顶。")

    def toggle_controls(self) -> None:
        self.controls_open = not self.controls_open
        if self.controls_open:
            self.controls.pack(fill="x", pady=(0, 0))
            self.root.geometry(f"352x360+{self.root.winfo_x()}+{self.root.winfo_y()}")
        else:
            self.controls.pack_forget()
            self.root.geometry(f"352x248+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def toggle_compact(self) -> None:
        if self.controls_open:
            self.toggle_controls()
        else:
            self.controls_open = True
            self.controls.pack(fill="x")
            self.root.geometry(f"352x360+{self.root.winfo_x()}+{self.root.winfo_y()}")

    def _set_status(self, text: str) -> None:
        if not self._closing:
            try:
                self.root.after(0, self._show_status, text)
            except tk.TclError:
                pass

    def _show_status(self, text: str) -> None:
        self.status.set(text)
        if text.startswith("已连接") or text.startswith("连接成功"):
            self.state_label.configure(text="GARMIN · CONNECTED", fg="#a7e3bd")
            self.state_dot.configure(fg="#63d391")
            self.disconnect_button.configure(state="normal")
        elif text.startswith("连接失败") or text.startswith("蓝牙连接已断开") or text.startswith("已断开"):
            self.state_label.configure(text="GARMIN · WAITING", fg="#c2d2c7")
            self.state_dot.configure(fg="#75867b")
            self.disconnect_button.configure(state="disabled")

    def _set_devices(self, values: list[str]) -> None:
        self.device_box["values"] = values
        if values:
            self.device_box.current(0)

    def scan(self) -> None:
        self.scan_button.configure(state="disabled")
        self.status.set("正在扫描附近的 BLE 设备…")
        future = self.worker.submit(self.worker.scan())
        future.add_done_callback(lambda _future: self.root.after(0, self.scan_button.configure, {"state": "normal"}))

    def connect(self) -> None:
        selected = self.device_box.get()
        self.connect_button.configure(state="disabled")
        self.worker.submit(self.worker.connect(selected)).add_done_callback(
            lambda _future: self.root.after(0, self._connection_buttons, True)
        )

    def disconnect(self) -> None:
        self.worker.submit(self.worker.disconnect())
        self.disconnect_button.configure(state="disabled")
        self.connect_button.configure(state="normal")

    def _connection_buttons(self, done: bool) -> None:
        if done:
            self.connect_button.configure(state="normal")
            self.disconnect_button.configure(state="normal" if self.worker.client else "disabled")

    def _set_bpm(self, bpm: int, timestamp: float) -> None:
        self.last_bpm_at = timestamp
        self.history.append((timestamp, bpm))
        while self.history and timestamp - self.history[0][0] > 60:
            self.history.popleft()
        values = [value for _, value in self.history]
        average = round(sum(values) / len(values))
        self.bpm_label.configure(text=str(bpm))
        self.average_label.configure(text=f"AVG  {average}")
        self.high_label.configure(text=f"HIGH  {max(values)}")
        self.last_update.configure(text="刚刚更新")
        self.state_label.configure(text="GARMIN · LIVE", fg="#a7e3bd")
        self.state_dot.configure(fg="#63d391")
        self.status.set("正在接收实时心率。")
        self._draw_chart()

    def _refresh_signal_state(self) -> None:
        if self._closing:
            return
        if self.last_bpm_at is not None:
            age = int(time.time() - self.last_bpm_at)
            if age > 5:
                self.last_update.configure(text=f"{age}s 前")
                self.state_label.configure(text="GARMIN · SIGNAL LOST", fg="#f1be74")
                self.state_dot.configure(fg="#e6ab59")
            else:
                self.last_update.configure(text="刚刚更新")
        self._draw_chart()
        self.root.after(1000, self._refresh_signal_state)

    def _draw_chart(self) -> None:
        canvas = self.chart
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 50 or height < 40:
            return
        now = time.time()
        while self.history and now - self.history[0][0] > 60:
            self.history.popleft()
        values = [bpm for _, bpm in self.history]
        left, right, top, bottom = 24, width - 3, 7, height - 15
        for fraction in (0, 0.5, 1):
            y = bottom - fraction * (bottom - top)
            canvas.create_line(left, y, right, y, fill="#26352e", width=1)
        canvas.create_text(left, height - 4, text="-60s", anchor="sw", fill="#71877a", font=("Segoe UI", 7))
        canvas.create_text((left + right) / 2, height - 4, text="-30s", anchor="s", fill="#71877a", font=("Segoe UI", 7))
        canvas.create_text(right, height - 4, text="now", anchor="se", fill="#71877a", font=("Segoe UI", 7))
        if not values:
            canvas.create_text(width / 2, (top + bottom) / 2, text="心率曲线将在连接后显示", fill="#74867b", font=("Segoe UI", 8))
            return
        low, high = max(35, min(values) - 8), max(values) + 8
        if high - low < 20:
            middle = (high + low) / 2
            low, high = max(35, middle - 10), middle + 10
        points = []
        for timestamp, bpm in self.history:
            x = left + max(0, min(1, (timestamp - (now - 60)) / 60)) * (right - left)
            y = bottom - (bpm - low) / (high - low) * (bottom - top)
            points.extend((x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill="#71dfa4", width=2, smooth=True, splinesteps=12)
        elif len(points) == 2:
            canvas.create_oval(points[0] - 3, points[1] - 3, points[0] + 3, points[1] + 3, fill="#71dfa4", outline="")

    def close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            future = self.worker.submit(self.worker.disconnect(update_status=False))
            future.result(timeout=3)
        except Exception:
            pass
        self.worker.loop.call_soon_threadsafe(self.worker.loop.stop)
        self.root.destroy()

    def _set_status(self, text: str) -> None:
        self.root.after(0, self.status.set, text)

    def _set_devices(self, values: list[str]) -> None:
        self.device_box["values"] = values
        if values:
            self.device_box.current(0)

    def scan(self) -> None:
        self.scan_button.configure(state="disabled")
        future = self.worker.submit(self.worker.scan())
        future.add_done_callback(lambda _future: self.root.after(0, self.scan_button.configure, {"state": "normal"}))

    def connect(self) -> None:
        selected = self.device_box.get()
        self.connect_button.configure(state="disabled")
        self.worker.submit(self.worker.connect(selected)).add_done_callback(
            lambda _future: self.root.after(0, self._connection_buttons, True)
        )

    def disconnect(self) -> None:
        self.worker.submit(self.worker.disconnect())
        self.disconnect_button.configure(state="disabled")
        self.connect_button.configure(state="normal")

    def _connection_buttons(self, done: bool) -> None:
        if done:
            self.connect_button.configure(state="normal")
            self.disconnect_button.configure(state="normal" if self.worker.client else "disabled")

    def _set_bpm(self, bpm: int, timestamp: float) -> None:
        self.history.append((timestamp, bpm))
        self.samples.append(bpm)
        self.bpm_label.configure(text=str(bpm))
        elapsed = int(timestamp - self.history[0][0]) if self.history else 0
        average = round(sum(self.samples) / len(self.samples))
        self.meta_label.configure(text=f"最近 {len(self.samples)} 次 · 均值 {average} bpm · 已接收 {elapsed} 秒")
        self._draw_chart()

    def _draw_chart(self) -> None:
        canvas = self.chart
        canvas.delete("all")
        width, height = canvas.winfo_width(), canvas.winfo_height()
        if width < 50 or height < 50:
            return
        values = [bpm for _, bpm in self.history]
        if not values:
            canvas.create_text(width / 2, height / 2, text="等待心率数据", fill="#89968f", font=("Segoe UI", 10))
            return
        low = max(30, min(values) - 8)
        high = max(values) + 8
        if high - low < 20:
            middle = (high + low) / 2
            low, high = max(30, middle - 10), middle + 10
        left, right, top, bottom = 38, width - 14, 16, height - 24
        for fraction in (0, 0.5, 1):
            y = bottom - fraction * (bottom - top)
            label = round(low + fraction * (high - low))
            canvas.create_line(left, y, right, y, fill="#e9eeeb")
            canvas.create_text(22, y, text=str(label), fill="#829088", font=("Segoe UI", 8))
        if len(values) == 1:
            points = [right, bottom - (values[0] - low) / (high - low) * (bottom - top)]
        else:
            points = []
            for index, value in enumerate(values):
                x = left + index / (len(values) - 1) * (right - left)
                y = bottom - (value - low) / (high - low) * (bottom - top)
                points.extend((x, y))
        if len(points) >= 4:
            canvas.create_line(*points, fill="#1e8b63", width=2, smooth=True)
        elif len(points) == 2:
            canvas.create_oval(points[0] - 3, points[1] - 3, points[0] + 3, points[1] + 3, fill="#1e8b63", outline="")

    def close(self) -> None:
        try:
            future = self.worker.submit(self.worker.disconnect(update_status=False))
            future.result(timeout=3)
        except Exception:
            pass
        self.worker.loop.call_soon_threadsafe(self.worker.loop.stop)
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    HeartRateMonitor(root)
    root.mainloop()


if __name__ == "__main__":
    main()
