"""Irabelle Waste Management: bin status from motion iBeacons."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .entity import hub_device_info
from .hub import WasteHub
from .services import async_setup_services

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.UPDATE,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type WasteConfigEntry = ConfigEntry[WasteHub]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the beacon services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WasteConfigEntry) -> bool:
    """Set up the hub and its bins."""
    hub = WasteHub(hass, entry)
    await hub.async_setup()
    # Register the hub device first: the bin devices link to it by its device id.
    hub.device_id = (
        dr.async_get(hass)
        .async_get_or_create(config_entry_id=entry.entry_id, **hub_device_info(hub))
        .id
    )
    entry.runtime_data = hub
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WasteConfigEntry) -> bool:
    """Unload the hub."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_unload()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: WasteConfigEntry) -> None:
    """Reload when options change or a bin is added, edited or removed."""
    await hass.config_entries.async_reload(entry.entry_id)
