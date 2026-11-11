"""Windows UI regression checks; no Bluetooth adapter or device is required."""

import ctypes
import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes

import pytest

from app import BleHeartRateReceiver, HeartRateMonitor, ScannedDeviceItem

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop UI")


@pytest.fixture
def monitor(monkeypatch):
    monkeypatch.setattr(BleHeartRateReceiver, "start", lambda self: None)

    class TrayIcon:
        def __init__(self, name, image, title, menu):
            self.menu = menu
            self.started = False
            self.stopped = False

        def run_detached(self):
            self.started = True

        def stop(self):
            self.stopped = True

    monkeypatch.setattr("pystray.Icon", TrayIcon)
    root = tk.Tk()
    app = HeartRateMonitor(root)
    root.update()
    yield app
    app.close_app()


def test_full_and_collapsed_panels_keep_controls_visible(monitor):
    for widget in (monitor.drag_handle, monitor.status_pill, monitor.btn_to_mini,
                   monitor.btn_opacity, monitor.btn_pin, monitor.btn_drawer, monitor.btn_close):
        assert widget.winfo_ismapped()
        assert widget.winfo_width() >= widget.winfo_reqwidth()
    for widget in (monitor.btn_scan, monitor.btn_connect, monitor.btn_disconnect):
        assert widget.winfo_ismapped()
    monitor.set_drawer_expanded(False)
    monitor.root.update()
    assert monitor.root.winfo_height() == 176
    assert monitor.plot_canvas.winfo_y() + monitor.plot_canvas.winfo_height() < 176
    assert not monitor.drawer_frame.winfo_ismapped()


def test_mini_modes_keep_readout_stable_for_two_and_three_digits(monitor):
    for mode, size, anchor in (("PILL", (138, 50), [122.0, 25.0]),
                               ("HUD", (138, 50), [122.0, 25.0]),
                               ("SPARK", (196, 48), [114.0, 24.0])):
        monitor.set_display_mode(mode)
        monitor.root.update()
        for bpm in (76, 168):
            monitor._ingest_hr_sample(bpm, time.time())
            monitor.root.update()
            assert (monitor.root.winfo_width(), monitor.root.winfo_height()) == size
            number = monitor.mini_canvas.find_withtag("mini_bpm")[0]
            assert monitor.mini_canvas.coords(number) == anchor
            assert monitor.mini_canvas.itemcget(number, "text").strip() == str(bpm)


@pytest.mark.parametrize("mode", ["PILL", "HUD", "SPARK"])
def test_hover_toolbar_does_not_move_the_mini_readout(monitor, mode):
    monitor.set_display_mode(mode)
    monitor._sync_window_geometry(x=140, y=140)
    monitor.root.update()
    position = (monitor.root.winfo_x(), monitor.root.winfo_y(),
                monitor.root.winfo_width(), monitor.root.winfo_height(),
                monitor.mini_canvas.winfo_rootx(), monitor.mini_canvas.winfo_rooty())
    monitor._on_mini_mouse_enter()
    monitor.root.update()
    assert (monitor.root.winfo_x(), monitor.root.winfo_y(),
            monitor.root.winfo_width(), monitor.root.winfo_height(),
            monitor.mini_canvas.winfo_rootx(), monitor.mini_canvas.winfo_rooty()) == position
    assert monitor.mini_hover_bar.winfo_ismapped()
    assert (monitor.mini_hover_bar.winfo_rooty() + monitor.mini_hover_bar.winfo_height()
            <= monitor.mini_canvas.winfo_rooty())
    monitor._hide_mini_hover_bar()
    monitor.root.update()
    assert not monitor.mini_hover_bar.winfo_ismapped()
    assert (monitor.root.winfo_x(), monitor.root.winfo_y(),
            monitor.root.winfo_width(), monitor.root.winfo_height(),
            monitor.mini_canvas.winfo_rootx(), monitor.mini_canvas.winfo_rooty()) == position


def test_taskbar_button_and_icons_survive_view_and_attribute_changes(monitor):
    user32 = ctypes.WinDLL("user32")
    user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.SendMessageW.restype = ctypes.c_ssize_t

    for mode in ("FULL", "PILL", "HUD", "SPARK"):
        monitor.set_display_mode(mode)
        monitor.toggle_topmost()
        monitor.cycle_opacity()
        monitor.root.update()
        hwnd = user32.GetAncestor(monitor.root.winfo_id(), 2)
        style = user32.GetWindowLongW(hwnd, -20)
        assert style & 0x00040000, "The window must have WS_EX_APPWINDOW"
        assert not style & 0x00000080, "WS_EX_TOOLWINDOW hides the taskbar button"
        assert user32.SendMessageW(hwnd, 0x007F, 0, 0), "Small taskbar icon is missing"
        assert user32.SendMessageW(hwnd, 0x007F, 1, 0), "Large taskbar icon is missing"


def test_stale_reading_keeps_last_value_until_fresh_data_arrives(monitor):
    monitor._ingest_hr_sample(144, time.time() - 14)
    monitor._periodic_tick()
    assert monitor.state_key == "SIGNAL_LOST"
    assert monitor.lbl_bpm_value.cget("text").strip() == "144"
    assert "过期" in monitor.lbl_freshness.cget("text")
    monitor._ingest_hr_sample(145, time.time())
    assert monitor.state_key == "LIVE"
    assert "刚刚更新" in monitor.lbl_freshness.cget("text")


def test_busy_actions_cannot_start_duplicate_operations(monitor, monkeypatch):
    calls = []
    monkeypatch.setattr(monitor.ble, "trigger_scan", lambda **kwargs: calls.append("scan"))
    monkeypatch.setattr(monitor.ble, "trigger_connect", lambda item: calls.append("connect"))
    monitor.on_click_scan()
    monitor.on_click_scan()
    assert calls == ["scan"]
    monitor._update_device_listbox([ScannedDeviceItem(address="AA:BB:CC:DD:EE:FF", name="BLE Heart Sensor")])
    monitor._set_state("DEVICES_FOUND")
    monitor.on_click_connect()
    monitor.on_click_connect()
    assert calls == ["scan", "connect"]


def test_long_device_caption_fits_and_production_cannot_load_demo(monitor):
    monitor.connected_device_name = "非常长的设备名称" * 10
    monitor._apply_state_visuals()
    monitor.root.update()
    caption = monitor.lbl_device_caption.cget("text")
    font = tkfont.Font(font=monitor.lbl_device_caption.cget("font"))
    assert caption.endswith("…")
    assert font.measure(caption) < monitor.meta_frame.winfo_width()
    monitor.load_preset_state("LIVE_3DIGIT")
    assert monitor.current_bpm is None
    assert not monitor._demo_sim_active


@pytest.mark.parametrize("samples", [[100] * 25, [76 + i % 8 for i in range(25)]])
def test_spark_pill_draws_recent_samples_and_preserves_stale_reading(monitor, samples):
    monitor.set_display_mode("SPARK")
    for index, bpm in enumerate(samples):
        monitor._ingest_hr_sample(bpm, time.time() - 25 + index)
    line = monitor.mini_canvas.find_withtag("mini_sparkline")[0]
    coords = monitor.mini_canvas.coords(line)
    assert len(coords) == 40  # Only the latest 20 BLE samples are shown.
    assert coords[0] == 128
    assert coords[-2] == 178
    assert all(13 <= y <= 35 for y in coords[1::2])
    assert monitor.mini_canvas.itemcget(line, "fill") == "#10B981"
    monitor._set_state("SIGNAL_LOST")
    line = monitor.mini_canvas.find_withtag("mini_sparkline")[0]
    assert monitor.mini_canvas.coords(line) == coords
    assert monitor.mini_canvas.itemcget(line, "fill") == "#F59E0B"
    number = monitor.mini_canvas.find_withtag("mini_bpm")[0]
    assert monitor.mini_canvas.itemcget(number, "text").strip() == str(samples[-1])


def test_hud_can_return_to_spark_pill(monitor):
    monitor.set_display_mode("SPARK")
    monitor.toggle_pill_or_hud()
    assert monitor.display_mode == "HUD"
    monitor.toggle_pill_or_hud()
    assert monitor.display_mode == "SPARK"


def test_tray_menu_routes_window_actions_to_tk_thread(monitor):
    def choose(label):
        item = next(item for item in monitor.tray_icon.menu.items if item.text == label)
        item(monitor.tray_icon)
        assert not monitor._ui_events.empty()
        callback, args = monitor._ui_events.get_nowait()
        callback(*args)
        monitor.root.update()

    assert monitor.tray_icon.started
    choose("隐藏悬浮窗")
    assert monitor.root.state() == "withdrawn"
    choose("极简带曲线 · 方案 C")
    assert monitor.root.state() == "normal"
    assert monitor.display_mode == "SPARK"
    assert (monitor.root.winfo_width(), monitor.root.winfo_height()) == (196, 48)


def test_exit_stops_tray_icon(monitor):
    icon = monitor.tray_icon
    monitor.close_app()
    assert icon.stopped
    assert monitor.tray_icon is None
