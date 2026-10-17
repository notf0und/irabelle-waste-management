"""KBeacon settings model: export/import templates (pure, no I/O).

A beacon configuration as read with ``{"msg": "getPara"}`` looks like::

    {"name": ..., "meaPwr": ..., "model": ..., "advObj": [{"slot": 0, ...}],
     "trObj": [{"trIdx": 0, ...}], "srObj": [{"srType": 1, ...}]}

An *export* keeps only what can be written and is meant to be shared between
beacons. It never carries the identity of a beacon (name, password, iBeacon
UUID/major/minor, Eddystone namespace/instance), so importing it into another
beacon keeps that beacon's identity.
"""

from __future__ import annotations

import copy
from typing import Any

EXPORT_FORMAT = "irabelle-kbeacon-settings"
EXPORT_VERSION = 1

ADV_KEY = "advObj"
TRIGGER_KEY = "trObj"
SENSOR_KEY = "srObj"

ADV_TYPE_NULL = 0
ADV_TYPE_IBEACON = 5
TRIGGER_TYPE_NULL = 0

# Reported by the beacon, never written.
READ_ONLY_COMMON = {
    "model", "ver", "hver", "bCap", "trCap", "maxPwr", "minPwr", "maxPrd",
    "maxSlot", "maxTg", "btPt", "id", "msg",
}
# Identity of one beacon: kept on import, never exported.
IDENTITY_COMMON = {"name", "pwd"}
IDENTITY_ADV = {"uuid", "majorID", "minorID", "nid", "sid"}


def writable_common(config: dict[str, Any]) -> dict[str, Any]:
    """Common (top-level) settings that can be written back."""
    return {
        key: value
        for key, value in config.items()
        if key not in READ_ONLY_COMMON
        and key not in (ADV_KEY, TRIGGER_KEY, SENSOR_KEY)
    }


def export_settings(config: dict[str, Any]) -> dict[str, Any]:
    """Return a shareable template of a beacon's configuration."""
    common = {
        key: value
        for key, value in writable_common(config).items()
        if key not in IDENTITY_COMMON
    }
    adv = [
        {k: v for k, v in slot.items() if k not in IDENTITY_ADV}
        for slot in config.get(ADV_KEY, [])
    ]
    return {
        "format": EXPORT_FORMAT,
        "version": EXPORT_VERSION,
        "model": config.get("model"),
        "firmware": config.get("ver"),
        "common": common,
        ADV_KEY: adv,
        TRIGGER_KEY: copy.deepcopy(config.get(TRIGGER_KEY, [])),
        SENSOR_KEY: copy.deepcopy(config.get(SENSOR_KEY, [])),
    }


class SettingsImportError(ValueError):
    """The template cannot be applied to this beacon."""


def build_import(
    template: dict[str, Any], current: dict[str, Any], allow_other_model: bool = False
) -> dict[str, Any]:
    """Return the ``cfg`` payload applying ``template`` to a beacon.

    ``current`` is the beacon's configuration as read just before. The
    beacon keeps its name, password and per-slot identity; slots and
    triggers that exist on the beacon but not in the template are disabled,
    so the beacon ends up behaving exactly like the template.
    """
    if template.get("format") != EXPORT_FORMAT:
        raise SettingsImportError("Not an Irabelle KBeacon settings export")
    if template.get("version", 0) > EXPORT_VERSION:
        raise SettingsImportError("This export was made by a newer version of the integration")
    model = template.get("model")
    if model and current.get("model") and model != current.get("model") and not allow_other_model:
        raise SettingsImportError(
            f"The export is for a {model}, this beacon is a {current.get('model')}"
        )

    max_slot = current.get("maxSlot")
    max_trigger = current.get("maxTg")
    payload: dict[str, Any] = dict(template.get("common", {}))
    for key in IDENTITY_COMMON | READ_ONLY_COMMON:
        payload.pop(key, None)

    # Identity per slot; a slot the beacon did not have borrows the identity of
    # the beacon's first slot that has one (e.g. its iBeacon UUID).
    current_slots = {s.get("slot"): s for s in current.get(ADV_KEY, [])}
    fallback_identity: dict[str, Any] = {}
    for slot in current.get(ADV_KEY, []):
        identity = {k: v for k, v in slot.items() if k in IDENTITY_ADV}
        if identity:
            fallback_identity = identity
            break

    adv: list[dict[str, Any]] = []
    template_slots: set[int] = set()
    for slot in template.get(ADV_KEY, []):
        index = slot.get("slot")
        if index is None or (max_slot is not None and index >= max_slot):
            raise SettingsImportError(f"Slot {index} does not exist on this beacon")
        template_slots.add(index)
        merged = {k: v for k, v in slot.items() if k not in IDENTITY_ADV}
        identity_source = current_slots.get(index, {})
        identity = {k: v for k, v in identity_source.items() if k in IDENTITY_ADV}
        if merged.get("type") == ADV_TYPE_IBEACON and "uuid" not in identity:
            identity = {k: v for k, v in fallback_identity.items() if k in ("uuid", "majorID", "minorID")}
            if "uuid" not in identity:
                raise SettingsImportError(
                    f"Slot {index} is an iBeacon in the export but this beacon has no "
                    "iBeacon UUID to keep; set one on the beacon first"
                )
        merged.update(identity)
        adv.append(merged)
    for index in current_slots:
        if index not in template_slots and current_slots[index].get("type") != ADV_TYPE_NULL:
            adv.append({"slot": index, "type": ADV_TYPE_NULL})

    triggers: list[dict[str, Any]] = []
    template_triggers: set[int] = set()
    for trigger in template.get(TRIGGER_KEY, []):
        index = trigger.get("trIdx")
        if index is None or (max_trigger is not None and index >= max_trigger):
            raise SettingsImportError(f"Trigger {index} does not exist on this beacon")
        template_triggers.add(index)
        triggers.append(dict(trigger))
    for trigger in current.get(TRIGGER_KEY, []):
        index = trigger.get("trIdx")
        if index not in template_triggers and trigger.get("trType") != TRIGGER_TYPE_NULL:
            triggers.append({"trIdx": index, "trType": TRIGGER_TYPE_NULL})

    if adv:
        payload[ADV_KEY] = sorted(adv, key=lambda s: s["slot"])
    if triggers:
        payload[TRIGGER_KEY] = sorted(triggers, key=lambda t: t["trIdx"])
    if sensors := template.get(SENSOR_KEY):
        payload[SENSOR_KEY] = copy.deepcopy(sensors)
    return payload
