"""The identity a bin tracks and the motion trigger behind it."""

from __future__ import annotations

import copy

import pytest

from custom_components.irabelle_waste_management.kbeacon.identity import (
    identity,
    motion_detection_ready,
    motion_uuid,
    setup_payload,
    supports_motion,
)


def test_identity_read_from_beacon(k4p, bc04p) -> None:
    assert identity(k4p) == {
        "slot": 0, "uuid": "7777772E-BC57-2928-1E6E-636F6D000005", "majorID": 5, "minorID": 5
    }
    assert motion_detection_ready(k4p) and motion_detection_ready(bc04p)
    assert supports_motion(k4p) and supports_motion(bc04p)


def test_motion_uuid_is_last_byte_plus_one() -> None:
    assert motion_uuid("1b6295d5-4f74-4c58-a2d8-cd83ca26bdf4") == "1B6295D5-4F74-4C58-A2D8-CD83CA26BDF5"


def test_nothing_to_write_when_ready(bc04p) -> None:
    current = identity(bc04p)
    assert setup_payload(bc04p, current["uuid"].lower(), current["majorID"], current["minorID"], True) == {}


def test_creates_motion_trigger(k4p) -> None:
    fresh = copy.deepcopy(k4p)
    fresh["trObj"] = []
    payload = setup_payload(fresh, identity(fresh)["uuid"], 5, 5, True)
    assert payload == {"trObj": [{
        "trIdx": 0, "trType": 1, "trAct": 1, "trAChg": 1, "trATm": 30, "trAPrd": 1000.0,
        "trAPwr": 8, "trPara": 50, "odr": 2, "ocnt": 1, "slot": 0,
    }]}


def test_fixes_trigger_without_uuid_change(k4p) -> None:
    beacon = copy.deepcopy(k4p)
    beacon["trObj"][0].update(trAChg=0, trPara=80)
    trigger = setup_payload(beacon, identity(beacon)["uuid"], 5, 5, True)["trObj"][0]
    assert trigger["trAChg"] == 1 and trigger["trPara"] == 80


def test_beacon_without_ibeacon_slot(k4p) -> None:
    beacon = copy.deepcopy(k4p)
    beacon["advObj"] = [{"slot": 0, "type": 3, "advPrd": 5000}]
    beacon["trObj"] = []
    payload = setup_payload(beacon, "7777772e-6b6b-6d63-6e2e-636f6d000001", 1, 1, True)
    assert payload["advObj"][0]["type"] == 5 and payload["trObj"][0]["slot"] == 0


def test_invalid_uuid(bc04p) -> None:
    with pytest.raises(ValueError):
        setup_payload(bc04p, "nope", 1, 1, False)
