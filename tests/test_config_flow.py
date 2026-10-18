"""Add-bin and Configure screens, driven with a fake beacon."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.irabelle_waste_management import config_flow as cf
from custom_components.irabelle_waste_management.beacon import BeaconError, BeaconImportError
from custom_components.irabelle_waste_management.config_flow import BinSubentryFlow
from custom_components.irabelle_waste_management.ibeacon import IBeaconFrame
from custom_components.irabelle_waste_management.kbeacon.settings import (
    SettingsImportError,
    build_import,
    export_settings,
)
from homeassistant.helpers import config_validation as cv

try:  # Home Assistant 2026.10+
    import probatio

    def to_fields(schema):
        return probatio.to_field_list(schema, custom_serializer=cv.custom_serializer)
except ImportError:  # pragma: no cover - older Home Assistant
    import voluptuous_serialize

    def to_fields(schema):
        return voluptuous_serialize.convert(schema, custom_serializer=cv.custom_serializer)


def serialized(result: dict[str, Any]) -> dict[str, Any]:
    """Fail like the frontend would if a form cannot be serialized."""
    if result["type"] == "form":
        to_fields(result["data_schema"])
    return result


class FakeBeacons:
    def __init__(self, config: dict[str, Any]) -> None:
        self.cfg = copy.deepcopy(config)
        self.writes: list[Any] = []

    async def async_read(self, bin_):
        return copy.deepcopy(self.cfg)

    async def async_write(self, bin_, payload):
        self.writes.append(payload)
        return copy.deepcopy(self.cfg)

    async def async_import(self, bin_, template, allow):
        try:
            payload = build_import(template, self.cfg, allow)
        except SettingsImportError as err:
            raise BeaconImportError(str(err)) from err
        self.writes.append(payload)
        return self.cfg

    async def async_identify(self, bin_):
        self.writes.append("ring")

    async def async_change_password(self, bin_, password):
        bin_.password = password
        self.writes.append({"pwd": password})

    async def async_reset(self, bin_):
        self.writes.append("reset")

    async def async_power_off(self, bin_):
        self.writes.append("off")

    def config(self, bin_):
        return self.cfg

    def supports_identify(self, bin_):
        return bool(int(self.cfg.get("bCap", 0)) & 0x2)


SUBENTRY_DATA = {
    "address": "BC:57:29:28:1E:6E",
    "idle_uuid": "7777772e-bc57-2928-1e6e-636f6d000005",
    "motion_uuid": "auto",
}


def make_flow(config: dict[str, Any], data: dict[str, Any] | None = None):
    flow = BinSubentryFlow()
    bin_ = SimpleNamespace(password="0000000000000000", name="Glass", capacity_mah=1000.0, efficiency=75.0)
    hub = SimpleNamespace(
        bins={"sid": bin_},
        beacons=FakeBeacons(config),
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_update_subentry=lambda *a, **k: None)),
    )
    subentry = SimpleNamespace(subentry_id="sid", title="Glass", data=dict(data or SUBENTRY_DATA))
    entry = SimpleNamespace(runtime_data=hub)
    flow._get_entry = lambda: entry
    flow._get_reconfigure_subentry = lambda: subentry
    flow.async_update_and_abort = lambda e, s, **kw: {"type": "abort", "reason": "updated", **kw}
    flow.hass = None
    return flow, hub


@pytest.mark.parametrize("model", ["k4p", "bc04p"])
async def test_configure_screens(model, request) -> None:
    config = request.getfixturevalue(model)
    flow, hub = make_flow(config)
    menu = await flow.async_step_reconfigure()
    assert "beacon_general" in menu["menu_options"]
    assert "beacon_identify" not in menu["menu_options"]  # no beeper on K4P/BC04P
    assert "beacon_sensor_select" not in menu["menu_options"]  # accelerometer only

    assert serialized(await flow.async_step_beacon_general())["type"] == "form"
    result = await flow.async_step_beacon_general({"name": "NewName", "meaPwr": -60, "atPwr": False})
    assert result["reason"] == "beacon_saved" and hub.beacons.writes[-1]["name"] == "NewName"
    result = await flow.async_step_beacon_general({"name": "X" * 19, "meaPwr": -60, "atPwr": False})
    assert result["errors"] == {"name": "name_length"}

    current = config["advObj"][0]
    await flow.async_step_beacon_slot_select()
    serialized(await flow.async_step_beacon_slot_select({"slot": "0", "type": "5"}))
    slot_input = {"advPrd": 1000, "txPwr": "-8", "conn": True, "trAdv": False, "mode": "0",
                  "disabled_channels": [], "uuid": current["uuid"], "majorID": current["majorID"],
                  "minorID": current["minorID"]}
    assert (await flow.async_step_beacon_slot(slot_input))["reason"] == "beacon_saved"
    assert hub.beacons.writes[-1]["advObj"][0]["txPwr"] == -8
    result = await flow.async_step_beacon_slot({**slot_input, "disabled_channels": ["37", "38", "39"]})
    assert result["errors"] == {"disabled_channels": "all_channels_disabled"}

    await flow.async_step_beacon_trigger_select()
    serialized(await flow.async_step_beacon_trigger_select({"trigger": "0", "type": "1"}))
    await flow.async_step_beacon_trigger({"trAct": ["1"], "slot": "0", "trAChg": True, "trATm": 60,
                                          "trAPrd": 1000, "trAPwr": "8", "trPara": 40, "odr": "2", "ocnt": 1})
    assert hub.beacons.writes[-1]["trObj"][0]["trPara"] == 40

    template = next(iter(serialized(await flow.async_step_beacon_export())["data_schema"].schema)).default()
    assert (await flow.async_step_beacon_import({"template": template, "allow_other_model": False}))["reason"] == "beacon_saved"
    assert (await flow.async_step_beacon_import({"template": "{x", "allow_other_model": False}))["errors"] == {"template": "invalid_json"}

    power = serialized(await flow.async_step_beacon_power())
    assert float(power["description_placeholders"]["current"]) > 0

    assert (await flow.async_step_beacon_password({"new_password": "short", "confirm_password": "short"}))["errors"] == {"new_password": "password_length"}
    result = await flow.async_step_beacon_password({"new_password": "abcdefgh1", "confirm_password": "abcdefgh1"})
    assert result["data"]["beacon_password"] == "abcdefgh1"
    assert (await flow.async_step_beacon_reset({"confirm": False}))["errors"] == {"confirm": "confirm_required"}
    assert (await flow.async_step_beacon_reset({"confirm": True}))["data"]["beacon_password"] == "0000000000000000"
    assert (await flow.async_step_beacon_power_off({"confirm": True}))["reason"] == "power_off_done"


async def test_power_profiler_matches_kbeacon_pro(k4p) -> None:
    flow, _ = make_flow(k4p)
    placeholders = (await flow.async_step_beacon_power())["description_placeholders"]
    assert (placeholders["current"], placeholders["months"]) == ("0.0171", "60.9")


async def test_cross_model_import_refused(k4p, bc04p) -> None:
    flow, _ = make_flow(bc04p)
    result = await flow.async_step_beacon_import({"template": json.dumps(export_settings(k4p)), "allow_other_model": False})
    assert result["errors"] == {"template": "import_rejected"}


async def test_identity_screen_updates_bin(k4p) -> None:
    flow, hub = make_flow(k4p)
    assert serialized(await flow.async_step_beacon_identity())["description_placeholders"]["motion"] == "ready"
    result = await flow.async_step_beacon_identity({"uuid": "AAAAAAAA-0000-0000-0000-000000000001", "majorID": 3, "minorID": 4})
    assert result["data"]["idle_uuid"] == "aaaaaaaa-0000-0000-0000-000000000001"
    assert hub.beacons.writes[-1]["advObj"][0]["majorID"] == 3


async def test_sensor_screen_with_temperature_beacon(k4p) -> None:
    beacon = copy.deepcopy(k4p)
    beacon["srObj"] = [{"srType": 2, "log": 1, "msItvl": 3, "tsThd": 5, "hsThd": 30, "dPrd0": 0}]
    beacon["trCap"] = (1 << 7) | (1 << 1) | 1
    flow, hub = make_flow(beacon)
    await flow.async_step_beacon_sensor_select()
    serialized(await flow.async_step_beacon_sensor_select({"sensor": "2"}))
    await flow.async_step_beacon_sensor({"log": True, "msItvl": 10, "tsThd": 0.3, "hsThd": 2.5, "dPrd0_on": False})
    assert hub.beacons.writes[-1] == {"srObj": [{"srType": 2, "log": 1, "msItvl": 10, "tsThd": 3, "hsThd": 25, "dPrd0": 0}]}
    await flow.async_step_beacon_trigger_select()
    serialized(await flow.async_step_beacon_trigger_select({"trigger": "2", "type": "2"}))
    await flow.async_step_beacon_trigger({"trAct": ["1"], "slot": "0", "trAChg": False, "trATm": 30, "trAPrd": 500,
                                          "trAPwr": "0", "trPara": -30, "aAng": 60, "rptPrd": 5})
    trigger = hub.beacons.writes[-1]["trObj"][0]
    assert (trigger["trPara"], trigger["aAng"], trigger["rptPrd"]) == (-30, 60, 5)


async def test_bin_settings_keeps_password_and_uuids(k4p) -> None:
    flow, _ = make_flow(k4p, {**SUBENTRY_DATA, "beacon_password": "secret123"})
    form = serialized(await flow.async_step_bin_settings())
    assert "secret123" not in json.dumps(to_fields(form["data_schema"]), default=str)
    result = await flow.async_step_bin_settings({"name": "Glass", "battery_capacity": 1000})
    assert result["data"]["beacon_password"] == "secret123"
    assert result["data"]["idle_uuid"] == SUBENTRY_DATA["idle_uuid"]
    result = await flow.async_step_bin_settings({"name": "Glass", "beacon_password": "123"})
    assert result["errors"] == {"beacon_password": "password_length"}


@pytest.fixture
def add_flow(k4p, monkeypatch):
    state = {"cfg": copy.deepcopy(k4p), "fail": False, "log": []}

    async def fake_session(hass, address, password, name, action):
        state["log"].append((address, password))
        if state["fail"]:
            raise BeaconError(f"{name}: login failed (Wrong password)")

        class Beacon:
            async def read_config(self, sensors=True):
                return copy.deepcopy(state["cfg"])

            async def write_config(self, payload):
                state["log"].append(("write", payload))

        return await action(Beacon())

    monkeypatch.setattr(cf, "async_beacon_session", fake_session)
    frame = IBeaconFrame("7777772e-bc57-2928-1e6e-636f6d000005", 5, 5, -59)
    monkeypatch.setattr(cf, "_discovered_ibeacons", lambda hass, exclude: {"BC:57:29:28:1E:6E": (frame, "Aaa123", -40)})
    entry = SimpleNamespace(subentries={})

    def new():
        flow = cf.BinSubentryFlow()
        flow.hass = None
        flow._get_entry = lambda: entry
        flow.async_create_entry = lambda **kw: {"type": "create_entry", **kw}
        return flow

    return new, state


async def test_add_bin_reads_identity(add_flow) -> None:
    new, state = add_flow
    flow = new()
    serialized(await flow.async_step_user())
    result = serialized(await flow.async_step_user({"name": "Glass", "address": "BC:57:29:28:1E:6E"}))
    assert result["step_id"] == "beacon_setup" and "setup_motion" not in str(result["data_schema"].schema)
    result = await flow.async_step_beacon_setup({"uuid": "7777772E-BC57-2928-1E6E-636F6D000005", "majorID": 5, "minorID": 5})
    assert result["data"]["idle_uuid"] == "7777772e-bc57-2928-1e6e-636f6d000005"
    assert not any(entry[0] == "write" for entry in state["log"])


async def test_add_bin_sets_up_motion(add_flow) -> None:
    new, state = add_flow
    state["cfg"]["trObj"] = []
    flow = new()
    await flow.async_step_user({"name": "Client", "address": "BC:57:29:28:1E:6E", "beacon_password": "maconha13"})
    assert "setup_motion" in str((await flow.async_step_beacon_setup())["data_schema"].schema)
    result = await flow.async_step_beacon_setup({"uuid": "7777772E-BC57-2928-1E6E-636F6D000005", "majorID": 9, "minorID": 5, "setup_motion": True})
    write = [entry for entry in state["log"] if entry[0] == "write"][-1][1]
    assert write["advObj"][0]["majorID"] == 9 and write["trObj"][0]["trAChg"] == 1
    assert result["data"]["beacon_password"] == "maconha13"


async def test_add_bin_without_connecting(add_flow) -> None:
    new, state = add_flow
    state["fail"] = True
    flow = new()
    result = serialized(await flow.async_step_user({"name": "Glass", "address": "BC:57:29:28:1E:6E"}))
    assert result["errors"] == {"base": "beacon_unreachable"} and "skip_beacon" in str(result["data_schema"].schema)
    assert (await flow.async_step_user({"name": "Glass", "address": "BC:57:29:28:1E:6E", "skip_beacon": True}))["step_id"] == "manual"
    result = await flow.async_step_manual({"battery_capacity": 1000})
    assert result["data"]["idle_uuid"] == "7777772e-bc57-2928-1e6e-636f6d000005"
