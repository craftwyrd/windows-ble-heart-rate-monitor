import pytest

from app import ScannedDeviceItem, parse_ble_heart_rate, parse_heart_rate


def test_parse_eight_bit_heart_rate():
    assert parse_heart_rate(bytes([0x00, 148])) == 148


def test_parse_sixteen_bit_heart_rate():
    assert parse_heart_rate(bytes([0x01, 0x2C, 0x01])) == 300


@pytest.mark.parametrize("payload", [b"", bytes([0x00]), bytes([0x01, 0x10])])
def test_reject_incomplete_measurements(payload):
    with pytest.raises(ValueError):
        parse_heart_rate(payload)


@pytest.mark.parametrize("payload", [b"", b"\x00", b"\x01\x10"])
def test_notification_parser_ignores_incomplete_measurements(payload):
    assert parse_ble_heart_rate(payload) is None


def test_device_without_name_keeps_identifiable_address():
    item = ScannedDeviceItem(address="AA:BB:CC:DD:EE:FF", name="")
    assert "未知设备" in item.display_title
    assert "EE:FF" in item.display_title


def test_long_device_name_preserves_heart_rate_service_tag():
    item = ScannedDeviceItem(address="AA:BB:CC:DD:EE:FF", name="通用心率广播测试设备" * 4,
                             has_hr_service=True)
    assert "…" in item.display_title
    assert item.display_title.endswith("[心率 0x180D]")
    assert len(item.display_title) < 35
