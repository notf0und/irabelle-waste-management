"""Buttons to identify a bin's beacon and refresh its settings."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WasteConfigEntry
from .entity import BinEntity
from .hub import Bin, WasteHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WasteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the beacon buttons of every bin."""
    hub = entry.runtime_data
    for bin_ in hub.bins.values():
        async_add_entities(
            [IdentifyButton(hub, bin_), ReadSettingsButton(hub, bin_)],
            config_subentry_id=bin_.subentry_id,
        )


class IdentifyButton(BinEntity, ButtonEntity):
    """Flash the beacon's LED for 10 seconds."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the button."""
        super().__init__(hub, bin_, "identify")

    async def async_press(self) -> None:
        """Flash the LED."""
        await self.hub.beacons.async_identify(self.bin)


class ReadSettingsButton(BinEntity, ButtonEntity):
    """Read every setting from the beacon (model, firmware, slots, triggers)."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the button."""
        super().__init__(hub, bin_, "read_settings")

    async def async_press(self) -> None:
        """Read and cache the settings."""
        await self.hub.beacons.async_read(self.bin)
