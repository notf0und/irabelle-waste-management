"""KBeacon sensor history (pure): request messages and record parsing.

Ported from KKM's SDK (KBRecordDataHandler.java, KBRecord*.java). These go
over the binary ("hex") channel. All integers are big-endian.

    read info     -> 01 <type>
    read records  -> 02 <type> <pos:4> <count:2> <order> 24
    clear records -> 03 <type>

    info reply    <- 01 <type> <total:4> <unread:4> <beacon utc seconds:4>
    records reply <- 02 <length:2> <type> <next pos:4> <records...>
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

MSG_INFO = 1
MSG_RECORDS = 2
MSG_CLEAR = 3

ORDER_NORMAL = 0  # oldest first
ORDER_REVERSE = 1  # newest first
ORDER_NEW = 2  # records not read before

INVALID_POS = 0xFFFFFFFF  # "start where appropriate" / "no more records"
MIN_UTC_SECONDS = 946080000  # earlier timestamps are relative to the beacon clock
HISTORY_CAPABILITY = 0x100000  # KBCfgCommon.isSupportHistoryRecord

SENSOR_HT = 0x2
SENSOR_ALARM = 0x8
SENSOR_PIR = 0x10
SENSOR_LIGHT = 0x20
SENSOR_VOC = 0x40
SENSOR_CO2 = 0x41

RECORD_LENGTH = {
    SENSOR_HT: 8,
    SENSOR_ALARM: 5,
    SENSOR_PIR: 5,
    SENSOR_LIGHT: 8,
    SENSOR_VOC: 8,
    SENSOR_CO2: 10,
}


class HistoryParseError(ValueError):
    """A sensor history reply could not be parsed."""


def supports_history(config: dict[str, Any]) -> bool:
    """True when the beacon records sensor history."""
    return bool(int(config.get("bCap", 0)) & HISTORY_CAPABILITY)


def info_request(sensor_type: int) -> bytes:
    """Ask how many records a sensor holds."""
    return bytes([MSG_INFO, sensor_type])


def records_request(
    sensor_type: int, position: int = INVALID_POS, count: int = 100, order: int = ORDER_NEW
) -> bytes:
    """Ask for up to ``count`` records starting at ``position``."""
    return (
        bytes([MSG_RECORDS, sensor_type])
        + (position & 0xFFFFFFFF).to_bytes(4, "big")
        + (count & 0xFFFF).to_bytes(2, "big")
        + bytes([order, 24])  # 24 x 1.25 ms: fast connection interval while reading
    )


def clear_request(sensor_type: int) -> bytes:
    """Delete a sensor's records."""
    return bytes([MSG_CLEAR, sensor_type])


def parse_info(data: bytes) -> dict[str, int]:
    """Parse a read-info reply."""
    if len(data) < 14 or data[0] != MSG_INFO:
        raise HistoryParseError("Invalid sensor info reply")
    return {
        "sensor_type": data[1],
        "total": int.from_bytes(data[2:6], "big"),
        "unread": int.from_bytes(data[6:10], "big"),
        "beacon_utc": int.from_bytes(data[10:14], "big"),
    }


def _fixed_8_8(high: int, low: int) -> float:
    value = (high << 8) | low
    if value >= 0x8000:
        value -= 0x10000
    return round(value / 256, 2)


def _timestamp(raw: int, utc_offset: int) -> str:
    seconds = raw + utc_offset if raw < MIN_UTC_SECONDS else raw
    return datetime.fromtimestamp(seconds, tz=UTC).isoformat()


def _record(sensor_type: int, chunk: bytes, utc_offset: int) -> dict[str, Any]:
    record: dict[str, Any] = {"time": _timestamp(int.from_bytes(chunk[0:4], "big"), utc_offset)}
    body = chunk[4:]
    if sensor_type == SENSOR_HT:
        record["temperature"] = _fixed_8_8(body[0], body[1])
        record["humidity"] = _fixed_8_8(body[2], body[3])
    elif sensor_type == SENSOR_LIGHT:
        record["type"] = body[0]
        record["lux"] = int.from_bytes(body[2:4], "big")
    elif sensor_type == SENSOR_PIR:
        record["detected"] = bool(body[0])
    elif sensor_type == SENSOR_ALARM:
        record["alarm"] = body[0]
    elif sensor_type == SENSOR_VOC:
        record["voc_index"] = int.from_bytes(body[0:2], "big")
        record["nox_index"] = int.from_bytes(body[2:4], "big")
    elif sensor_type == SENSOR_CO2:
        record["co2"] = int.from_bytes(body[0:2], "big")
        record["temperature"] = _fixed_8_8(body[2], body[3])
        record["humidity"] = _fixed_8_8(body[4], body[5])
    return record


def parse_records(data: bytes, utc_offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Parse one records reply -> (records, next position)."""
    if len(data) < 8 or data[0] != MSG_RECORDS:
        raise HistoryParseError("Invalid sensor records reply")
    if int.from_bytes(data[1:3], "big") != len(data) - 3:
        raise HistoryParseError("Sensor records reply has the wrong length")
    sensor_type = data[3]
    if (length := RECORD_LENGTH.get(sensor_type)) is None:
        raise HistoryParseError(f"Unknown sensor type {sensor_type}")
    next_position = int.from_bytes(data[4:8], "big")
    payload = data[8:]
    if len(payload) % length:
        raise HistoryParseError("Truncated sensor records")
    records = [
        _record(sensor_type, payload[i : i + length], utc_offset)
        for i in range(0, len(payload), length)
    ]
    return records, next_position
