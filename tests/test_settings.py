"""Settings export/import templates."""

from __future__ import annotations

import copy

import pytest

from custom_components.irabelle_waste_management.kbeacon.settings import (
    SettingsImportError,
    build_import,
    export_settings,
)


def test_export_strips_identity(k4p) -> None:
    template = export_settings(k4p)
    assert template["common"] == {"atPwr": 0, "meaPwr": -59}
    slot = template["advObj"][0]
    assert "uuid" not in slot and "majorID" not in slot and slot["txPwr"] == -16
    assert template["trObj"][0]["trPara"] == 50


def test_import_keeps_identity_and_disables_extras(k4p) -> None:
    other = copy.deepcopy(k4p)
    other["advObj"] = [
        {"slot": 0, "type": 5, "advPrd": 2000, "txPwr": 0, "conn": 1,
         "uuid": "AAAA0000-0000-0000-0000-000000000009", "majorID": 9, "minorID": 9},
        {"slot": 1, "type": 3, "advPrd": 5000},
    ]
    other["trObj"] = [{"trIdx": 0, "trType": 1, "trPara": 10}, {"trIdx": 1, "trType": 4}]
    payload = build_import(export_settings(k4p), other)
    assert "name" not in payload and "pwd" not in payload
    slot0 = payload["advObj"][0]
    assert slot0["uuid"].startswith("AAAA") and slot0["majorID"] == 9 and slot0["txPwr"] == -16
    assert payload["advObj"][1] == {"slot": 1, "type": 0}
    assert payload["trObj"][1] == {"trIdx": 1, "trType": 0}


def test_import_onto_itself_changes_nothing(k4p) -> None:
    payload = build_import(export_settings(k4p), k4p)
    assert payload["advObj"][0] == k4p["advObj"][0]
    assert payload["trObj"] == k4p["trObj"]


def test_other_model_needs_permission(k4p, bc04p) -> None:
    with pytest.raises(SettingsImportError):
        build_import(export_settings(k4p), bc04p)
    assert build_import(export_settings(k4p), bc04p, allow_other_model=True)["advObj"]


def test_slot_out_of_range(k4p) -> None:
    template = export_settings(k4p)
    template["advObj"][0]["slot"] = 7
    with pytest.raises(SettingsImportError):
        build_import(template, k4p)
