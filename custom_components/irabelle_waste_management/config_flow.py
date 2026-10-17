"""Config flow for Irabelle Waste Management.

One config entry is the hub (collection window and timing rules); every bin is
a subentry added from the hub's "Add bin" button.
"""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_NAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
    TimeSelector,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_ADDRESS,
    CONF_AWAY_MINUTES,
    CONF_BEACON_PASSWORD,
    CONF_CALENDAR,
    CONF_IDLE_UUID,
    CONF_KEYWORD,
    CONF_MOTION_UUID,
    CONF_RETURN_SECONDS,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_AWAY_MINUTES,
    DEFAULT_RETURN_SECONDS,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    MOTION_UUID_AUTO,
    SUBENTRY_BIN,
)
from .beacon_flow import BeaconStepsMixin
from .ibeacon import IBeaconFrame, frame_from_service_info, normalize_uuid
from .kbeacon.protocol import validate_password

DEFAULT_TITLE = "Irabelle Waste Management"
_MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def _hub_schema(values: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_WINDOW_START,
                default=values.get(CONF_WINDOW_START, DEFAULT_WINDOW_START),
            ): TimeSelector(),
            vol.Required(
                CONF_WINDOW_END, default=values.get(CONF_WINDOW_END, DEFAULT_WINDOW_END)
            ): TimeSelector(),
            vol.Required(
                CONF_AWAY_MINUTES,
                default=values.get(CONF_AWAY_MINUTES, DEFAULT_AWAY_MINUTES),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=1, max=120, step=1, unit_of_measurement="min",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            vol.Required(
                CONF_RETURN_SECONDS,
                default=values.get(CONF_RETURN_SECONDS, DEFAULT_RETURN_SECONDS),
            ): NumberSelector(
                NumberSelectorConfig(
                    min=10, max=1800, step=10, unit_of_measurement="s",
                    mode=NumberSelectorMode.BOX,
                )
            ),
        }
    )


def _validate_hub(user_input: dict[str, Any]) -> dict[str, str]:
    start = dt_util.parse_time(user_input[CONF_WINDOW_START])
    end = dt_util.parse_time(user_input[CONF_WINDOW_END])
    if start is None or end is None or start >= end:
        return {CONF_WINDOW_END: "window_order"}
    return {}


class IrabelleWasteConfigFlow(ConfigFlow, domain=DOMAIN):
    """Create the hub."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the hub name and timing rules."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (errors := _validate_hub(user_input)):
                name = user_input.pop(CONF_NAME).strip() or DEFAULT_TITLE
                return self.async_create_entry(title=name, data={}, options=user_input)
        schema = vol.Schema(
            {vol.Required(CONF_NAME, default=DEFAULT_TITLE): TextSelector()}
        ).extend(_hub_schema(user_input or {}).schema)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Edit the timing rules."""
        return IrabelleWasteOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Bins are subentries of the hub."""
        return {SUBENTRY_BIN: BinSubentryFlow}


class IrabelleWasteOptionsFlow(OptionsFlow):
    """Edit the hub timing rules."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the timing rules."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not (errors := _validate_hub(user_input)):
                return self.async_create_entry(data=user_input)
        values = user_input or dict(self.config_entry.options)
        return self.async_show_form(
            step_id="init", data_schema=_hub_schema(values), errors=errors
        )


class BinSubentryFlow(BeaconStepsMixin, ConfigSubentryFlow):
    """Add or reconfigure a bin."""

    def __init__(self) -> None:
        """Initialise the flow."""
        self._address: str | None = None
        self._name: str | None = None
        self._seen: dict[str, tuple[IBeaconFrame, str | None, int | None]] = {}

    # -- add ---------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the beacon (from what the proxies hear) and name the bin."""
        configured = {
            sub.data[CONF_ADDRESS].upper()
            for sub in self._get_entry().subentries.values()
            if sub.subentry_type == SUBENTRY_BIN
        }
        self._seen = _discovered_ibeacons(self.hass, exclude=configured)
        errors: dict[str, str] = {}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper().replace("-", ":")
            if not _MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            elif address in configured:
                return self.async_abort(reason="already_configured")
            else:
                self._address = address
                self._name = user_input[CONF_NAME].strip()
                return await self.async_step_details()

        options = [
            SelectOptionDict(
                value=address,
                label=(
                    f"{name + ' · ' if name and name != address else ''}{address}"
                    f" · {frame.uuid} ({frame.major}/{frame.minor})"
                    f"{f' · {rssi} dBm' if rssi is not None else ''}"
                ),
            )
            # Strongest first: the bin being set up is usually the nearest.
            for address, (frame, name, rssi) in sorted(
                self._seen.items(), key=lambda item: -(item[1][2] or -200)
            )
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Required(CONF_ADDRESS): SelectSelector(
                    SelectSelectorConfig(
                        options=options,
                        custom_value=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders={"count": str(len(options))},
        )

    async def async_step_details(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Beacon UUIDs and the collection calendar."""
        assert self._address is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _validate_details(user_input)
            if not errors:
                data[CONF_ADDRESS] = self._address
                return self.async_create_entry(
                    title=self._name or self._address,
                    data=data,
                    unique_id=self._address,
                )

        suggested = user_input or {}
        if not suggested and (seen := self._seen.get(self._address)):
            frame = seen[0]
            # The beacon is normally at rest, so what we hear is its idle frame.
            suggested = {CONF_IDLE_UUID: frame.uuid}
        return self.async_show_form(
            step_id="details",
            data_schema=self.add_suggested_values_to_schema(
                _details_schema(), {CONF_MOTION_UUID: MOTION_UUID_AUTO, **suggested}
            ),
            errors=errors,
            description_placeholders={"address": self._address},
        )

    # -- reconfigure -----------------------------------------------------

    async def async_step_bin_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Edit a bin (the MAC address stays the same)."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input.pop(CONF_NAME).strip()
            data, errors = _validate_details(user_input)
            if not errors:
                data[CONF_ADDRESS] = subentry.data[CONF_ADDRESS]
                if CONF_BEACON_PASSWORD not in data and (
                    stored := subentry.data.get(CONF_BEACON_PASSWORD)
                ):
                    data[CONF_BEACON_PASSWORD] = stored
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=name or subentry.title, data=data
                )
        schema = vol.Schema({vol.Required(CONF_NAME): TextSelector()}).extend(
            _details_schema().schema
        )
        values = user_input or {
            CONF_NAME: subentry.title,
            **{k: v for k, v in subentry.data.items() if k != CONF_BEACON_PASSWORD},
        }
        return self.async_show_form(
            step_id="bin_settings",
            data_schema=self.add_suggested_values_to_schema(schema, values),
            errors=errors,
            description_placeholders={"address": subentry.data[CONF_ADDRESS]},
        )


def _details_schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_IDLE_UUID): TextSelector(),
            vol.Required(CONF_MOTION_UUID, default=MOTION_UUID_AUTO): TextSelector(),
            vol.Optional(CONF_CALENDAR): EntitySelector(
                EntitySelectorConfig(domain="calendar")
            ),
            vol.Optional(CONF_KEYWORD): TextSelector(),
            vol.Optional(CONF_BEACON_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
    )


def _validate_details(
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    data: dict[str, Any] = {}
    try:
        data[CONF_IDLE_UUID] = normalize_uuid(user_input[CONF_IDLE_UUID])
    except ValueError:
        errors[CONF_IDLE_UUID] = "invalid_uuid"

    motion = user_input.get(CONF_MOTION_UUID, "").strip().lower() or MOTION_UUID_AUTO
    if motion == MOTION_UUID_AUTO:
        data[CONF_MOTION_UUID] = MOTION_UUID_AUTO
    else:
        try:
            data[CONF_MOTION_UUID] = normalize_uuid(motion)
        except ValueError:
            errors[CONF_MOTION_UUID] = "invalid_uuid"
        else:
            if data[CONF_MOTION_UUID] == data.get(CONF_IDLE_UUID):
                errors[CONF_MOTION_UUID] = "same_uuid"

    if password := (user_input.get(CONF_BEACON_PASSWORD) or "").strip():
        try:
            validate_password(password)
        except ValueError:
            errors[CONF_BEACON_PASSWORD] = "password_length"
        else:
            data[CONF_BEACON_PASSWORD] = password

    if calendar := user_input.get(CONF_CALENDAR):
        data[CONF_CALENDAR] = calendar
        data[CONF_KEYWORD] = (user_input.get(CONF_KEYWORD) or "").strip()
    return data, errors


@callback
def _discovered_ibeacons(
    hass, exclude: set[str]
) -> dict[str, tuple[IBeaconFrame, str | None, int | None]]:
    """iBeacons the Bluetooth proxies currently hear, by MAC address."""
    found: dict[str, tuple[IBeaconFrame, str | None, int | None]] = {}
    for info in bluetooth.async_discovered_service_info(hass, connectable=False):
        address = info.address.upper()
        if address in exclude:
            continue
        if _is_random_private(info):
            # Rotating addresses (phones, beacons in config mode) cannot be
            # tracked by MAC; they can still be typed in by hand.
            continue
        if (frame := frame_from_service_info(info)) is not None:
            found[address] = (frame, info.name, info.rssi)
    return found


def _is_random_private(info) -> bool:
    """True for a resolvable private address (rotates, cannot be tracked by MAC).

    Proxies report the address type (0 public, 1 random); only random addresses
    whose two top bits are 01 are resolvable private addresses.
    """
    details = getattr(info.device, "details", None) or {}
    if details.get("address_type") != 1:
        return False
    return (int(info.address[:2], 16) >> 6) == 0b01
