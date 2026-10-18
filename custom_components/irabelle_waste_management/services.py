"""Services to configure the bins' KBeacons."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr

from .const import CONF_BEACON_PASSWORD, DOMAIN, STATUSES

if TYPE_CHECKING:
    from .hub import Bin, WasteHub

ATTR_DEVICE_ID = "device_id"
ATTR_SETTINGS = "settings"
ATTR_REFRESH = "refresh"
ATTR_ALLOW_OTHER_MODEL = "allow_other_model"
ATTR_DURATION = "duration"
ATTR_CONFIRM = "confirm"
ATTR_PASSWORD = "password"
ATTR_STATUS = "status"
ATTR_SENSOR = "sensor"
ATTR_NEW_ONLY = "new_only"
ATTR_MAX_RECORDS = "max_records"

HISTORY_SENSORS = {
    "temperature_humidity": 0x2,
    "alarm": 0x8,
    "pir": 0x10,
    "light": 0x20,
    "voc": 0x40,
    "co2": 0x41,
}

_DEVICE = {vol.Required(ATTR_DEVICE_ID): cv.string}
_CONFIRM = {vol.Required(ATTR_CONFIRM): vol.All(cv.boolean, True)}


def _find_bin(hass: HomeAssistant, device_id: str) -> tuple[WasteHub, Bin]:
    device = dr.async_get(hass).async_get(device_id)
    if device is not None:
        for domain, identifier in device.identifiers:
            if domain != DOMAIN:
                continue
            for entry in hass.config_entries.async_loaded_entries(DOMAIN):
                hub: WasteHub = entry.runtime_data
                if bin_ := hub.bins.get(identifier):
                    return hub, bin_
    raise ServiceValidationError(f"{device_id} is not an Irabelle Waste Management bin")


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the beacon services."""

    async def read_settings(call: ServiceCall) -> ServiceResponse:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        config = await hub.beacons.async_read(bin_)
        return {"config": config}

    async def write_settings(call: ServiceCall) -> ServiceResponse:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        config = await hub.beacons.async_write(bin_, dict(call.data[ATTR_SETTINGS]))
        return {"config": config}

    async def export_settings(call: ServiceCall) -> ServiceResponse:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        settings = await hub.beacons.async_export(bin_, call.data[ATTR_REFRESH])
        return {"settings": settings}

    async def import_settings(call: ServiceCall) -> ServiceResponse:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        config = await hub.beacons.async_import(
            bin_, dict(call.data[ATTR_SETTINGS]), call.data[ATTR_ALLOW_OTHER_MODEL]
        )
        return {"config": config}

    async def identify(call: ServiceCall) -> None:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        await hub.beacons.async_identify(bin_, call.data[ATTR_DURATION])

    async def reset(call: ServiceCall) -> None:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        await hub.beacons.async_reset(bin_)

    async def power_off(call: ServiceCall) -> None:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        await hub.beacons.async_power_off(bin_)

    async def change_password(call: ServiceCall) -> None:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        new_password: str = call.data[ATTR_PASSWORD]
        try:
            await hub.beacons.async_change_password(bin_, new_password)
        finally:
            # Saved whenever the beacon took it, even if verifying failed.
            if bin_.password == new_password:
                _save_password(hass, hub, bin_, new_password)

    async def set_status(call: ServiceCall) -> None:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        hub.async_set_status(bin_, call.data[ATTR_STATUS])

    async def read_history(call: ServiceCall) -> ServiceResponse:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        return await hub.beacons.async_read_history(
            bin_,
            HISTORY_SENSORS[call.data[ATTR_SENSOR]],
            call.data[ATTR_NEW_ONLY],
            call.data[ATTR_MAX_RECORDS],
        )

    async def clear_history(call: ServiceCall) -> None:
        hub, bin_ = _find_bin(hass, call.data[ATTR_DEVICE_ID])
        await hub.beacons.async_clear_history(bin_, HISTORY_SENSORS[call.data[ATTR_SENSOR]])

    def _register(
        name: str,
        handler: Any,
        schema: dict[Any, Any],
        response: SupportsResponse = SupportsResponse.NONE,
    ) -> None:
        hass.services.async_register(
            DOMAIN, name, handler, schema=vol.Schema(schema), supports_response=response
        )

    _register("read_beacon_settings", read_settings, _DEVICE, SupportsResponse.OPTIONAL)
    _register(
        "write_beacon_settings",
        write_settings,
        {**_DEVICE, vol.Required(ATTR_SETTINGS): dict},
        SupportsResponse.OPTIONAL,
    )
    _register(
        "export_beacon_settings",
        export_settings,
        {**_DEVICE, vol.Optional(ATTR_REFRESH, default=True): cv.boolean},
        SupportsResponse.ONLY,
    )
    _register(
        "import_beacon_settings",
        import_settings,
        {
            **_DEVICE,
            vol.Required(ATTR_SETTINGS): dict,
            vol.Optional(ATTR_ALLOW_OTHER_MODEL, default=False): cv.boolean,
        },
        SupportsResponse.OPTIONAL,
    )
    _register(
        "identify_beacon",
        identify,
        {**_DEVICE, vol.Optional(ATTR_DURATION, default=10): vol.All(int, vol.Range(1, 60))},
    )
    _register(
        "read_beacon_history",
        read_history,
        {
            **_DEVICE,
            vol.Required(ATTR_SENSOR): vol.In(HISTORY_SENSORS),
            vol.Optional(ATTR_NEW_ONLY, default=True): cv.boolean,
            vol.Optional(ATTR_MAX_RECORDS, default=500): vol.All(int, vol.Range(1, 5000)),
        },
        SupportsResponse.ONLY,
    )
    _register(
        "clear_beacon_history",
        clear_history,
        {**_DEVICE, vol.Required(ATTR_SENSOR): vol.In(HISTORY_SENSORS), **_CONFIRM},
    )
    _register("set_bin_status", set_status, {**_DEVICE, vol.Required(ATTR_STATUS): vol.In(STATUSES)})
    _register("reset_beacon", reset, {**_DEVICE, **_CONFIRM})
    _register("power_off_beacon", power_off, {**_DEVICE, **_CONFIRM})
    _register(
        "change_beacon_password",
        change_password,
        {**_DEVICE, vol.Required(ATTR_PASSWORD): vol.All(cv.string, vol.Length(8, 16))},
    )


def _save_password(hass: HomeAssistant, hub: WasteHub, bin_: Bin, password: str) -> None:
    subentry = hub.entry.subentries[bin_.subentry_id]
    hass.config_entries.async_update_subentry(
        hub.entry, subentry, data={**subentry.data, CONF_BEACON_PASSWORD: password}
    )
