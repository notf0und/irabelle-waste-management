"""iBeacon and KKM extension parsing."""

from __future__ import annotations

from types import SimpleNamespace

from custom_components.irabelle_waste_management.ibeacon import (
    kkm_battery_from_service_info,
    parse_raw_advertisement,
    parse_raw_kkm_battery,
)

# flags + Apple manufacturer data (iBeacon) exactly as an ESPHome proxy forwards it
RAW_IBEACON = bytes.fromhex(
    "0201061aff4c0002157777772ebc5729281e6e636f6d00000600050005c5"
)
RAW_SCAN_RESPONSE = bytes([9, 0x16, 0x80, 0x20]) + bytes.fromhex("690400010001")


def test_parse_ibeacon() -> None:
    frame = parse_raw_advertisement(RAW_IBEACON)
    assert frame is not None
    assert frame.uuid == "7777772e-bc57-2928-1e6e-636f6d000006"
    assert (frame.major, frame.minor, frame.power) == (5, 5, -59)
    assert parse_raw_advertisement(bytes.fromhex("020106")) is None


def test_battery_from_scan_response() -> None:
    assert parse_raw_kkm_battery(RAW_SCAN_RESPONSE) == 105
    assert parse_raw_kkm_battery(RAW_IBEACON) is None
    merged = SimpleNamespace(
        raw=None,
        service_data={"00002080-0000-1000-8000-00805f9b34fb": bytes.fromhex("680400020002")},
    )
    assert kkm_battery_from_service_info(merged) == 104
