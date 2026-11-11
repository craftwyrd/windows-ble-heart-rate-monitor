"""Check that monitor resources belong to its own Tcl interpreter."""

import sys
import tkinter as tk

import pytest

from app import BleHeartRateReceiver, HeartRateMonitor


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop UI")
def test_monitor_can_open_when_another_tk_root_exists(monkeypatch):
    monkeypatch.setattr(BleHeartRateReceiver, "start", lambda self: None)
    monkeypatch.setattr(HeartRateMonitor, "_start_tray", lambda self: None)
    other_root = tk.Tk()
    other_root.withdraw()
    root = tk.Tk()
    monitor = None
    try:
        monitor = HeartRateMonitor(root)
        root.update()
        assert monitor._icon_image.tk is root.tk
        assert root.tk.call("image", "type", str(monitor._icon_image)) == "photo"
        monitor.connected_device_name = "Long device name " * 10
        monitor._apply_state_visuals()
        assert monitor.lbl_device_caption.cget("text").endswith("…")
    finally:
        if monitor is not None:
            monitor.close_app()
        else:
            root.destroy()
        other_root.destroy()
