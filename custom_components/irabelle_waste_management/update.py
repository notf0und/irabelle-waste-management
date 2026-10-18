"""Beacon firmware: installed version vs KKM's published firmware."""

from __future__ import annotations

from typing import Any

from homeassistant.components.update import UpdateDeviceClass, UpdateEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WasteConfigEntry
from .entity import BinEntity
from .hub import Bin, WasteHub
from .kbeacon.firmware import DOWNLOAD_URL


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WasteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a firmware entity to every bin."""
    hub = entry.runtime_data
    for bin_ in hub.bins.values():
        async_add_entities([BeaconFirmware(hub, bin_)], config_subentry_id=bin_.subentry_id)


class BeaconFirmware(BinEntity, UpdateEntity):
    """Shows when KKM publishes newer firmware for the beacon's hardware.

    Installing is done with the KBeacon Pro app for now.
    """

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_title = "KKM KBeacon firmware"

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the entity."""
        super().__init__(hub, bin_, "firmware")

    @property
    def available(self) -> bool:
        """Available once the beacon's settings have been read."""
        return self.hub.firmware(self.bin) is not None

    @property
    def installed_version(self) -> str | None:
        """Firmware on the beacon."""
        status = self.hub.firmware(self.bin)
        return status.installed if status else None

    @property
    def latest_version(self) -> str | None:
        """Newest firmware KKM offers for this hardware (else the installed one)."""
        status = self.hub.firmware(self.bin)
        if status is None:
            return None
        return status.latest if status.update_file else status.installed

    @property
    def release_summary(self) -> str | None:
        """KKM's notes for the newer versions."""
        status = self.hub.firmware(self.bin)
        if status is None or not status.update_file:
            return None
        return (status.notes or "")[:255] or None

    @property
    def release_url(self) -> str | None:
        """The firmware package."""
        status = self.hub.firmware(self.bin)
        if status is None or not status.update_file:
            return None
        return DOWNLOAD_URL.format(file=status.update_file)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Hardware version and what KKM publishes for it."""
        status = self.hub.firmware(self.bin)
        config = self.hub.beacons.config(self.bin) or {}
        return {
            "model": config.get("model"),
            "hardware_version": config.get("hver"),
            "published_for_hardware": status.hardware_listed if status else None,
            "latest_published": status.latest if status else None,
        }
