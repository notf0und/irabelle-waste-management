"""Manual status correction for each bin."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WasteConfigEntry
from .const import STATUSES
from .entity import BinEntity
from .hub import Bin, WasteHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WasteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a status selector to every bin."""
    hub = entry.runtime_data
    for bin_ in hub.bins.values():
        async_add_entities([StatusSelect(hub, bin_)], config_subentry_id=bin_.subentry_id)


class StatusSelect(BinEntity, SelectEntity):
    """Shows the bin status and lets it be corrected when detection got it wrong."""

    _attr_options = STATUSES
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the selector."""
        super().__init__(hub, bin_, "status_override")

    @property
    def current_option(self) -> str | None:
        """The current status."""
        return self.bin.tracker.status

    async def async_select_option(self, option: str) -> None:
        """Set the status by hand."""
        self.hub.async_set_status(self.bin, option)
