"""Binary sensors for Irabelle Waste Management."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WasteConfigEntry
from .const import STATUS_AWAITING, STATUS_EMPTIED
from .entity import BinEntity, HubEntity
from .hub import Bin, WasteHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WasteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the bin and hub binary sensors."""
    hub = entry.runtime_data
    async_add_entities([ReadyToBringBackBinarySensor(hub)])
    for bin_ in hub.bins.values():
        async_add_entities(
            [BinMotionBinarySensor(hub, bin_), BinInRangeBinarySensor(hub, bin_)],
            config_subentry_id=bin_.subentry_id,
        )


class BinMotionBinarySensor(BinEntity, BinarySensorEntity):
    """On while the beacon advertises its motion frame."""

    _attr_device_class = BinarySensorDeviceClass.MOVING

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the sensor."""
        super().__init__(hub, bin_, "motion")

    @property
    def is_on(self) -> bool:
        """Return True while motion is being advertised."""
        return self.bin.moving


class BinInRangeBinarySensor(BinEntity, BinarySensorEntity):
    """On while idle (normal) advertisements are being heard."""

    _attr_device_class = BinarySensorDeviceClass.PRESENCE

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the sensor."""
        super().__init__(hub, bin_, "in_range")

    @property
    def is_on(self) -> bool:
        """Return True while the bin is heard."""
        return self.bin.in_range


class ReadyToBringBackBinarySensor(HubEntity, BinarySensorEntity):
    """On once every bin that went out has been emptied."""

    def __init__(self, hub: WasteHub) -> None:
        """Initialise the sensor."""
        super().__init__(hub, "ready_to_bring_back")

    @property
    def is_on(self) -> bool:
        """Return True when no bin is still awaiting collection."""
        return self.hub.ready_to_bring_back()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List what is out there."""
        return {
            "emptied": [b.name for b in self.hub.bins_with_status(STATUS_EMPTIED)],
            "awaiting_collection": [
                b.name for b in self.hub.bins_with_status(STATUS_AWAITING)
            ],
        }
