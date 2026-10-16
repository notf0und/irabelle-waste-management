"""iBeacon frame parsing.

Kept free of Home Assistant imports so it can be tested on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

APPLE_COMPANY_ID = 0x004C
_IBEACON_PREFIX = b"\x02\x15"
_AD_TYPE_MANUFACTURER = 0xFF
_AD_TYPE_SERVICE_DATA_16 = 0x16
# KKM (KBeacon) extension data, sent in the scan response next to the iBeacon
# frame. Byte 0 is the battery level in percent (the KKM SDK clamps it to 100).
KKM_EXT_UUID16 = 0x2080
KKM_EXT_UUID = "00002080-0000-1000-8000-00805f9b34fb"


@dataclass(frozen=True, slots=True)
class IBeaconFrame:
    """The identifying part of one iBeacon advertisement."""

    uuid: str
    major: int
    minor: int
    power: int


def parse_apple_payload(payload: bytes) -> IBeaconFrame | None:
    """Parse Apple manufacturer data (without the company id) as an iBeacon."""
    if len(payload) < 23 or payload[:2] != _IBEACON_PREFIX:
        return None
    return IBeaconFrame(
        uuid=str(UUID(bytes=bytes(payload[2:18]))),
        major=int.from_bytes(payload[18:20], "big"),
        minor=int.from_bytes(payload[20:22], "big"),
        power=int.from_bytes(payload[22:23], "big", signed=True),
    )


def parse_raw_advertisement(raw: bytes) -> IBeaconFrame | None:
    """Find an iBeacon frame in raw advertisement bytes (AD structures)."""
    index = 0
    length = len(raw)
    while index < length:
        ad_len = raw[index]
        if ad_len == 0:
            break
        end = index + 1 + ad_len
        if end > length:
            break
        if raw[index + 1] == _AD_TYPE_MANUFACTURER and ad_len >= 3:
            company = int.from_bytes(raw[index + 2 : index + 4], "little")
            if company == APPLE_COMPANY_ID:
                return parse_apple_payload(raw[index + 4 : end])
        index = end
    return None


def parse_raw_kkm_battery(raw: bytes) -> int | None:
    """Return the raw KKM battery byte from raw advertisement bytes, if present."""
    index = 0
    length = len(raw)
    while index < length:
        ad_len = raw[index]
        if ad_len == 0:
            break
        end = index + 1 + ad_len
        if end > length:
            break
        if (
            raw[index + 1] == _AD_TYPE_SERVICE_DATA_16
            and ad_len >= 6
            and int.from_bytes(raw[index + 2 : index + 4], "little") == KKM_EXT_UUID16
        ):
            return raw[index + 4]
        index = end
    return None


def kkm_battery_from_service_info(service_info) -> int | None:
    """Return the raw KKM battery byte carried by this advertisement, if any."""
    if (raw := getattr(service_info, "raw", None)) is not None:
        return parse_raw_kkm_battery(raw)
    data = service_info.service_data.get(KKM_EXT_UUID)
    if data is not None and len(data) > 2:
        return data[0]
    return None


def frame_from_service_info(service_info) -> IBeaconFrame | None:
    """Return the iBeacon frame carried by *this* advertisement.

    ESPHome proxies give us the raw packet, which is the only reliable way to
    tell which frame arrived: ``manufacturer_data`` is merged across packets,
    so it can still show an older frame. Without raw bytes we fall back to it.
    """
    if (raw := getattr(service_info, "raw", None)) is not None:
        return parse_raw_advertisement(raw)
    if payload := service_info.manufacturer_data.get(APPLE_COMPANY_ID):
        return parse_apple_payload(payload)
    return None


def normalize_uuid(value: str) -> str:
    """Return the canonical lower-case, dashed form of a UUID string."""
    return str(UUID(value.strip()))
