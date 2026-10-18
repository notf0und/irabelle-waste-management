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
    BooleanSelector,
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
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_EFFICIENCY,
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
from .beacon_flow import BeaconStepsMixin, identity_fields as _identity_fields
from .ibeacon import IBeaconFrame, frame_from_service_info
from .kbeacon.power import DEFAULT_CAPACITY_MAH
from .beacon import BeaconError, async_beacon_session
from .kbeacon.identity import (
    identity,
    motion_detection_ready,
    normalize as identity_normalize,
    setup_payload,
    supports_motion,
)
from .kbeacon.protocol import DEFAULT_PASSWORD, validate_password

DEFAULT_TITLE = "Irabelle Waste Management"
KKM_DEFAULT_UUID = "7777772E-6B6B-6D63-6E2E-636F6D000001"
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
        self._password: str = DEFAULT_PASSWORD
        self._seen: dict[str, tuple[IBeaconFrame, str | None, int | None]] = {}
        self._read: dict[str, Any] | None = None
        self._offer_skip = False

    # -- add ---------------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Pick the beacon, name the bin; then read the beacon."""
        configured = {
            sub.data[CONF_ADDRESS].upper()
            for sub in self._get_entry().subentries.values()
            if sub.subentry_type == SUBENTRY_BIN
        }
        self._seen = _discovered_ibeacons(self.hass, exclude=configured)
        errors: dict[str, str] = {}
        placeholders = {"count": str(len(self._seen)), "error": ""}

        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper().replace("-", ":")
            password = (user_input.get(CONF_BEACON_PASSWORD) or "").strip() or DEFAULT_PASSWORD
            if not _MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_address"
            elif address in configured:
                return self.async_abort(reason="already_configured")
            try:
                validate_password(password)
            except ValueError:
                errors[CONF_BEACON_PASSWORD] = "password_length"
            if not errors:
                self._address = address
                self._name = user_input[CONF_NAME].strip() or address
                self._password = password
                if user_input.get("skip_beacon"):
                    return await self.async_step_manual()
                try:
                    self._read = await async_beacon_session(
                        self.hass, address, password, self._name,
                        lambda kb: kb.read_config(sensors=False),
                    )
                except BeaconError as err:
                    errors["base"] = "beacon_unreachable"
                    placeholders["error"] = str(err)
                    self._offer_skip = True
                else:
                    return await self.async_step_beacon_setup()

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
        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME): TextSelector(),
            vol.Required(CONF_ADDRESS): SelectSelector(
                SelectSelectorConfig(
                    options=options, custom_value=True, mode=SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Optional(CONF_BEACON_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
        }
        if self._offer_skip:
            fields[vol.Optional("skip_beacon", default=False)] = BooleanSelector()
        suggested = {k: v for k, v in (user_input or {}).items() if k != CONF_BEACON_PASSWORD}
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(vol.Schema(fields), suggested),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_beacon_setup(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """UUID/major/minor read from the beacon (written back if changed)."""
        assert self._address is not None and self._read is not None
        config = self._read
        offer_motion = supports_motion(config) and not motion_detection_ready(config)
        errors: dict[str, str] = {}
        placeholders = {**_beacon_placeholders(config), "error": ""}

        if user_input is not None:
            data, errors = _validate_common(user_input)
            try:
                uuid = identity_normalize(user_input["uuid"])
            except ValueError:
                errors["uuid"] = "invalid_uuid"
            if not errors:
                payload = setup_payload(
                    config,
                    uuid,
                    int(user_input["majorID"]),
                    int(user_input["minorID"]),
                    offer_motion and user_input.get("setup_motion", False),
                )
                if payload:
                    try:
                        await async_beacon_session(
                            self.hass, self._address, self._password, self._name or "",
                            lambda kb: kb.write_config(payload),
                        )
                    except BeaconError as err:
                        errors["base"] = "beacon_write_failed"
                        placeholders["error"] = str(err)
                if not errors:
                    data.update(
                        {
                            CONF_ADDRESS: self._address,
                            CONF_IDLE_UUID: uuid.lower(),
                            CONF_MOTION_UUID: MOTION_UUID_AUTO,
                            CONF_BEACON_PASSWORD: self._password,
                        }
                    )
                    return self.async_create_entry(
                        title=self._name or self._address,
                        data=data,
                        unique_id=self._address,
                    )

        current = identity(config) or _fallback_identity(self._seen.get(self._address))
        fields: dict[Any, Any] = _identity_fields(current)
        if offer_motion:
            fields[vol.Required("setup_motion", default=True)] = BooleanSelector()
        fields.update(_common_fields())
        return self.async_show_form(
            step_id="beacon_setup",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(fields), user_input or {}
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Add without connecting: follow the UUID the beacon advertises."""
        assert self._address is not None
        seen = self._seen.get(self._address)
        if seen is None:
            return self.async_abort(reason="not_heard")
        errors: dict[str, str] = {}
        if user_input is not None:
            data, errors = _validate_common(user_input)
            if not errors:
                data.update(
                    {
                        CONF_ADDRESS: self._address,
                        CONF_IDLE_UUID: seen[0].uuid.lower(),
                        CONF_MOTION_UUID: MOTION_UUID_AUTO,
                        CONF_BEACON_PASSWORD: self._password,
                    }
                )
                return self.async_create_entry(
                    title=self._name or self._address, data=data, unique_id=self._address
                )
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema(_common_fields()),
            errors=errors,
            description_placeholders={"uuid": seen[0].uuid.upper()},
        )

    # -- reconfigure -----------------------------------------------------

    async def async_step_bin_settings(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name, calendar, password and battery (the beacon's UUIDs live in
        the beacon screens)."""
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            name = user_input.pop(CONF_NAME).strip()
            data, errors = _validate_common(user_input)
            if not errors:
                kept = {
                    k: v
                    for k, v in subentry.data.items()
                    if k in (CONF_ADDRESS, CONF_IDLE_UUID, CONF_MOTION_UUID)
                    or (k == CONF_BEACON_PASSWORD and CONF_BEACON_PASSWORD not in data)
                    or (k == CONF_BATTERY_EFFICIENCY)
                }
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=name or subentry.title,
                    data={**kept, **data},
                )
        schema = vol.Schema({vol.Required(CONF_NAME): TextSelector()}).extend(
            _common_fields()
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


def _fallback_identity(seen: tuple[IBeaconFrame, str | None, int | None] | None) -> dict[str, Any]:
    if seen is not None:
        frame = seen[0]
        return {"uuid": frame.uuid.upper(), "majorID": frame.major, "minorID": frame.minor}
    return {"uuid": KKM_DEFAULT_UUID, "majorID": 1, "minorID": 1}


def _beacon_placeholders(config: dict[str, Any]) -> dict[str, str]:
    return {
        "model": str(config.get("model", "?")),
        "firmware": str(config.get("ver", "?")),
        "battery": str(config.get("btPt", "?")),
        "beacon_name": str(config.get("name", "")),
        "motion": "ready" if motion_detection_ready(config) else (
            "missing" if supports_motion(config) else "unsupported"
        ),
    }


def _common_fields() -> dict[Any, Any]:
    return {
        vol.Optional(CONF_CALENDAR): EntitySelector(EntitySelectorConfig(domain="calendar")),
        vol.Optional(CONF_KEYWORD): TextSelector(),
        vol.Optional(CONF_BATTERY_CAPACITY, default=DEFAULT_CAPACITY_MAH): NumberSelector(
            NumberSelectorConfig(
                min=10, max=20000, step=10, unit_of_measurement="mAh",
                mode=NumberSelectorMode.BOX,
            )
        ),
        vol.Optional(CONF_BEACON_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }


def _validate_common(
    user_input: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    """Calendar, battery capacity and (optional) beacon password."""
    errors: dict[str, str] = {}
    data: dict[str, Any] = {}
    if password := (user_input.get(CONF_BEACON_PASSWORD) or "").strip():
        try:
            validate_password(password)
        except ValueError:
            errors[CONF_BEACON_PASSWORD] = "password_length"
        else:
            data[CONF_BEACON_PASSWORD] = password
    if (capacity := user_input.get(CONF_BATTERY_CAPACITY)) is not None:
        data[CONF_BATTERY_CAPACITY] = float(capacity)
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
