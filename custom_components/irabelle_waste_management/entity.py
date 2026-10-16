"""Base entities for Irabelle Waste Management."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_BIN_UPDATED, SIGNAL_HUB_UPDATED
from .hub import Bin, WasteHub

MANUFACTURER = "Irabelle"


def hub_device_info(hub: WasteHub) -> DeviceInfo:
    """Device that groups the hub-level summary entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, hub.entry.entry_id)},
        name=hub.entry.title,
        manufacturer=MANUFACTURER,
        model="Waste management hub",
    )


class BinEntity(Entity):
    """An entity that belongs to one bin."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: WasteHub, bin_: Bin, key: str) -> None:
        """Initialise the entity for a bin."""
        self.hub = hub
        self.bin = bin_
        self._attr_translation_key = key
        self._attr_unique_id = f"{bin_.subentry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, bin_.subentry_id)},
            name=bin_.name,
            manufacturer=MANUFACTURER,
            model="Bin beacon",
            serial_number=bin_.address,
            via_device=(DOMAIN, hub.entry.entry_id),
        )

    async def async_added_to_hass(self) -> None:
        """Follow updates of the bin."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_BIN_UPDATED.format(self.bin.subentry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class HubEntity(Entity):
    """A summary entity across every bin of the hub."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hub: WasteHub, key: str) -> None:
        """Initialise the summary entity."""
        self.hub = hub
        self._attr_translation_key = key
        self._attr_unique_id = f"{hub.entry.entry_id}_{key}"
        self._attr_device_info = hub_device_info(hub)

    async def async_added_to_hass(self) -> None:
        """Follow updates of the hub."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                SIGNAL_HUB_UPDATED.format(self.hub.entry.entry_id),
                self._handle_update,
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()
