import pytest

from app import parse_heart_rate


def test_parse_eight_bit_heart_rate():
    assert parse_heart_rate(bytes([0x00, 148])) == 148


def test_parse_sixteen_bit_heart_rate():
    assert parse_heart_rate(bytes([0x01, 0x2C, 0x01])) == 300


@pytest.mark.parametrize("payload", [b"", bytes([0x00]), bytes([0x01, 0x10])])
def test_reject_incomplete_measurements(payload):
    with pytest.raises(ValueError):
        parse_heart_rate(payload)
