"""Sensor history records."""

from __future__ import annotations

import pytest

from custom_components.irabelle_waste_management.kbeacon import history


def fixed(value: float) -> bytes:
    return int(round(value * 256)).to_bytes(2, "big", signed=True)


def records_reply(sensor_type: int, next_pos: int, payload: bytes) -> bytes:
    body = bytes([sensor_type]) + next_pos.to_bytes(4, "big") + payload
    return bytes([2]) + len(body).to_bytes(2, "big") + body


def test_requests() -> None:
    assert history.info_request(2) == b"\x01\x02"
    assert history.records_request(2, history.INVALID_POS, 100, history.ORDER_NEW) == (
        b"\x02\x02\xff\xff\xff\xff\x00\x64\x02\x18"
    )
    assert history.clear_request(0x20) == b"\x03\x20"


def test_info() -> None:
    data = bytes([1, 2]) + (120).to_bytes(4, "big") + (7).to_bytes(4, "big") + (1_760_000_000).to_bytes(4, "big")
    assert history.parse_info(data) == {
        "sensor_type": 2, "total": 120, "unread": 7, "beacon_utc": 1_760_000_000
    }


def test_temperature_humidity_records() -> None:
    record = (1_760_000_000).to_bytes(4, "big") + fixed(21.5) + fixed(55.25)
    relative = (3600).to_bytes(4, "big") + fixed(-3.75) + fixed(80.0)
    records, next_pos = history.parse_records(
        records_reply(2, history.INVALID_POS, record + relative), utc_offset=1_700_000_000
    )
    assert next_pos == history.INVALID_POS
    assert records[0] == {"time": "2025-10-09T08:53:20+00:00", "temperature": 21.5, "humidity": 55.25}
    assert records[1]["temperature"] == -3.75
    assert records[1]["time"].startswith("2023-11-14")  # relative time + beacon offset


def test_other_record_types() -> None:
    t = (1_760_000_000).to_bytes(4, "big")
    light, _ = history.parse_records(records_reply(0x20, 5, t + bytes([1, 0]) + (900).to_bytes(2, "big")))
    assert light[0]["lux"] == 900
    pir, _ = history.parse_records(records_reply(0x10, 5, t + b"\x01"))
    assert pir[0]["detected"] is True
    co2, _ = history.parse_records(records_reply(0x41, 5, t + (812).to_bytes(2, "big") + fixed(22) + fixed(40)))
    assert co2[0] == {"time": co2[0]["time"], "co2": 812, "temperature": 22.0, "humidity": 40.0}


def test_bad_replies() -> None:
    with pytest.raises(history.HistoryParseError):
        history.parse_records(bytes([2, 0, 9, 2, 0, 0, 0, 0, 1, 2, 3, 4]))  # wrong length
    with pytest.raises(history.HistoryParseError):
        history.parse_info(b"\x01\x02")


def test_capability(k4p) -> None:
    assert not history.supports_history(k4p)
    assert history.supports_history({"bCap": history.HISTORY_CAPABILITY})
