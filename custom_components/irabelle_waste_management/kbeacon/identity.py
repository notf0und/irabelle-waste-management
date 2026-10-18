"""The iBeacon identity a bin tracks, and the motion trigger behind it (pure).

A bin follows one iBeacon slot: while still the beacon advertises that slot's
UUID; a motion trigger with "change UUID" (``trAChg``) makes the same slot
advertise a different UUID (KKM adds one to the last byte), which is how the
integration tells motion apart. These helpers find that slot and build the
settings that set it up.
"""

from __future__ import annotations

from typing import Any
import uuid as uuid_lib

ADV_TYPE_IBEACON = 5
TRIGGER_MOTION = 1
ACTION_ADVERTISE = 0x1

# A new iBeacon slot (when the beacon has none) and a new motion trigger.
NEW_SLOT_DEFAULTS = {"advPrd": 1000.0, "txPwr": 0, "conn": 1, "trAdv": 0}
MOTION_DEFAULTS = {
    "trAct": ACTION_ADVERTISE,
    "trAChg": 1,
    "trATm": 30,
    "trAPrd": 1000.0,
    "trAPwr": 0,
    "trPara": 50,
    "odr": 2,
    "ocnt": 1,
}


def _slots(config: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {s["slot"]: s for s in config.get("advObj", []) if "slot" in s}


def motion_trigger(config: dict[str, Any]) -> dict[str, Any] | None:
    """The first motion trigger that advertises, if any."""
    for trigger in config.get("trObj", []):
        if trigger.get("trType") == TRIGGER_MOTION and int(trigger.get("trAct", 0)) & ACTION_ADVERTISE:
            return trigger
    return None


def tracked_slot(config: dict[str, Any]) -> int | None:
    """The iBeacon slot a bin follows: the motion trigger's, else the first."""
    slots = _slots(config)
    if (trigger := motion_trigger(config)) is not None:
        slot = slots.get(trigger.get("slot"))
        if slot and slot.get("type") == ADV_TYPE_IBEACON:
            return slot["slot"]
    for index in sorted(slots):
        if slots[index].get("type") == ADV_TYPE_IBEACON:
            return index
    return None


def identity(config: dict[str, Any]) -> dict[str, Any] | None:
    """UUID/major/minor of the tracked slot, if the beacon has one."""
    if (index := tracked_slot(config)) is None:
        return None
    slot = _slots(config)[index]
    return {
        "slot": index,
        "uuid": str(slot.get("uuid", "")).upper(),
        "majorID": int(slot.get("majorID", 0)),
        "minorID": int(slot.get("minorID", 0)),
    }


def motion_detection_ready(config: dict[str, Any]) -> bool:
    """True when motion makes the tracked slot change its UUID."""
    index = tracked_slot(config)
    trigger = motion_trigger(config)
    return (
        index is not None
        and trigger is not None
        and trigger.get("slot") == index
        and bool(trigger.get("trAChg"))
    )


def supports_motion(config: dict[str, Any]) -> bool:
    """True when the beacon has an accelerometer motion trigger."""
    return bool(int(config.get("trCap", 0)) & (1 << (TRIGGER_MOTION - 1)))


def normalize(value: str) -> str:
    """Canonical upper-case UUID as the beacon reports it (ValueError if bad)."""
    return str(uuid_lib.UUID(value.strip())).upper()


def motion_uuid(value: str) -> str:
    """The UUID a KKM beacon advertises while a UUID-changing trigger fires."""
    raw = bytearray(uuid_lib.UUID(value).bytes)
    raw[-1] = (raw[-1] + 1) & 0xFF
    return str(uuid_lib.UUID(bytes=bytes(raw))).upper()


def setup_payload(
    config: dict[str, Any],
    uuid: str,
    major: int,
    minor: int,
    setup_motion: bool,
) -> dict[str, Any]:
    """Settings to write so the beacon has this identity (and motion trigger).

    Returns an empty dict when nothing needs to change.
    """
    payload: dict[str, Any] = {}
    slots = _slots(config)
    index = tracked_slot(config)
    wanted = {"uuid": normalize(uuid), "majorID": int(major), "minorID": int(minor)}

    if index is None:
        # No iBeacon slot: turn slot 0 into one.
        index = 0
        payload["advObj"] = [
            {"slot": 0, "type": ADV_TYPE_IBEACON, **NEW_SLOT_DEFAULTS, **wanted}
        ]
    else:
        current = slots[index]
        if (
            str(current.get("uuid", "")).upper() != wanted["uuid"]
            or int(current.get("majorID", -1)) != wanted["majorID"]
            or int(current.get("minorID", -1)) != wanted["minorID"]
        ):
            payload["advObj"] = [{"slot": index, "type": ADV_TYPE_IBEACON, **wanted}]

    if setup_motion and not (
        motion_detection_ready(config) and tracked_slot(config) == index
    ):
        existing = motion_trigger(config)
        if existing is not None:
            trigger_index = existing["trIdx"]
        else:
            used = {t.get("trIdx") for t in config.get("trObj", []) if t.get("trType")}
            trigger_index = next(i for i in range(int(config.get("maxTg", 5))) if i not in used)
        max_power = int(config.get("maxPwr", 0))
        payload["trObj"] = [
            {
                "trIdx": trigger_index,
                "trType": TRIGGER_MOTION,
                **MOTION_DEFAULTS,
                **{k: v for k, v in (existing or {}).items() if k in ("trATm", "trAPrd", "trAPwr", "trPara", "odr", "ocnt")},
                "trAct": ACTION_ADVERTISE | int((existing or {}).get("trAct", 0)),
                "trAChg": 1,
                "slot": index,
            }
        ]
        if existing is None:
            payload["trObj"][0]["trAPwr"] = min(max_power, 8)
    return payload
