"""Diagnostics: bins and the last settings read from their beacons."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import WasteConfigEntry
from .const import CONF_BEACON_PASSWORD

TO_REDACT = {CONF_BEACON_PASSWORD, "pwd"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WasteConfigEntry
) -> dict[str, Any]:
    """Return the bins, their status and their beacon settings."""
    hub = entry.runtime_data
    return {
        "options": dict(entry.options),
        "bins": {
            bin_.name: {
                "subentry": async_redact_data(
                    dict(entry.subentries[bin_.subentry_id].data), TO_REDACT
                ),
                "status": bin_.tracker.as_dict(),
                "next_collection": str(bin_.next_collection),
                "beacon": async_redact_data(
                    hub.beacons.cache.get(bin_.subentry_id, {}), TO_REDACT
                ),
            }
            for bin_ in hub.bins.values()
        },
    }
