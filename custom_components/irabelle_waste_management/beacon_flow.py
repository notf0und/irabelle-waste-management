"""Beacon configuration screens (mixed into the bin subentry flow).

Every screen reads the beacon live first, so the form always starts from the
beacon's current values; saving writes only what that screen covers and reads
everything back.
"""

from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import SubentryFlowResult
from homeassistant.const import CONF_NAME
from homeassistant.helpers.selector import (
    BooleanSelector,
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
)

from .beacon import BeaconError, BeaconImportError
from .const import (
    CONF_BATTERY_CAPACITY,
    CONF_BEACON_PASSWORD,
    CONF_IDLE_UUID,
    CONF_MOTION_UUID,
    MOTION_UUID_AUTO,
)
from .kbeacon import capabilities as caps
from .kbeacon.power import estimate as power_estimate
from .kbeacon.protocol import DEFAULT_PASSWORD, validate_password
from .kbeacon.settings import export_settings

if TYPE_CHECKING:
    from .hub import Bin, WasteHub

MAX_NAME_LENGTH = 18

BEACON_MENU = [
    "bin_settings",
    "beacon_general",
    "beacon_slot_select",
    "beacon_trigger_select",
    "beacon_export",
    "beacon_import",
    "beacon_power",
    "beacon_identify",
    "beacon_password",
    "beacon_reset",
    "beacon_power_off",
]


def _select(options: dict[Any, str] | list[SelectOptionDict], multiple: bool = False) -> SelectSelector:
    if isinstance(options, dict):
        options = [SelectOptionDict(value=str(k), label=v) for k, v in options.items()]
    return SelectSelector(
        SelectSelectorConfig(
            options=options, mode=SelectSelectorMode.DROPDOWN, multiple=multiple
        )
    )


def _number(low: float, high: float, step: float | str = 1, unit: str | None = None) -> NumberSelector:
    config = NumberSelectorConfig(min=low, max=high, step=step, mode=NumberSelectorMode.BOX)
    if unit:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


class BeaconStepsMixin:
    """Beacon screens for :class:`BinSubentryFlow`."""

    _beacon_config: dict[str, Any] | None = None
    _slot_index: int = 0
    _slot_type: int = 0
    _trigger_index: int = 0
    _trigger_type: int = 0

    # -- helpers ------------------------------------------------------------

    def _hub_and_bin(self) -> tuple[WasteHub, Bin] | None:
        entry = self._get_entry()  # type: ignore[attr-defined]
        hub = getattr(entry, "runtime_data", None)
        if hub is None:
            return None
        subentry = self._get_reconfigure_subentry()  # type: ignore[attr-defined]
        bin_ = hub.bins.get(subentry.subentry_id)
        return (hub, bin_) if bin_ else None

    async def _load(self) -> SubentryFlowResult | None:
        """Read the beacon; return an abort result when that is impossible."""
        found = self._hub_and_bin()
        if found is None:
            return self.async_abort(reason="not_loaded")  # type: ignore[attr-defined]
        hub, bin_ = found
        try:
            self._beacon_config = await hub.beacons.async_read(bin_)
        except BeaconError as err:
            return self.async_abort(  # type: ignore[attr-defined]
                reason="beacon_error", description_placeholders={"error": str(err)}
            )
        return None

    async def _write(self, payload: dict[str, Any]) -> SubentryFlowResult | None:
        hub, bin_ = self._hub_and_bin()  # type: ignore[misc]
        try:
            self._beacon_config = await hub.beacons.async_write(bin_, payload)
        except BeaconError as err:
            return self.async_abort(  # type: ignore[attr-defined]
                reason="beacon_error", description_placeholders={"error": str(err)}
            )
        return None

    def _done(self) -> SubentryFlowResult:
        return self.async_abort(reason="beacon_saved")  # type: ignore[attr-defined]

    def _common(self) -> dict[str, Any]:
        return self._beacon_config or {}

    # -- menu ---------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Bin settings plus everything the beacon can be told."""
        menu = list(BEACON_MENU)
        found = self._hub_and_bin()
        if found is None or not found[0].beacons.supports_identify(found[1]):
            # KBeacon Pro offers no ring/flash on beacons without a beeper.
            menu.remove("beacon_identify")
        return self.async_show_menu(  # type: ignore[attr-defined]
            step_id="reconfigure",
            menu_options=menu,
            description_placeholders={
                "address": self._get_reconfigure_subentry().data["address"]  # type: ignore[attr-defined]
            },
        )

    # -- general ------------------------------------------------------------

    async def async_step_beacon_general(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Name, measured power and LED behaviour."""
        if user_input is None:
            if (abort := await self._load()) is not None:
                return abort
        config = self._common()
        errors: dict[str, str] = {}
        if user_input is not None and not 1 <= len(user_input[CONF_NAME].strip()) <= MAX_NAME_LENGTH:
            errors[CONF_NAME] = "name_length"
        if user_input is not None and not errors:
            payload: dict[str, Any] = {
                "name": user_input[CONF_NAME].strip(),
                "meaPwr": int(user_input["meaPwr"]),
                "atPwr": 1 if user_input["atPwr"] else 0,
            }
            if "nBlk" in config:
                payload["nBlk"] = int(user_input["nBlk"])
            if "lBlk" in config:
                payload["lBlk"] = 1 if user_input["lBlk"] else 0
            if (abort := await self._write(payload)) is not None:
                return abort
            return self._done()

        fields: dict[Any, Any] = {
            vol.Required(CONF_NAME, default=config.get("name", "")): TextSelector(),
            vol.Required("meaPwr", default=config.get("meaPwr", -59)): _number(-100, 0, 1, "dBm"),
            vol.Required("atPwr", default=bool(config.get("atPwr"))): BooleanSelector(),
        }
        if "nBlk" in config:
            fields[vol.Required("nBlk", default=config["nBlk"])] = _number(0, 255, 1)
        if "lBlk" in config:
            fields[vol.Required("lBlk", default=bool(config["lBlk"]))] = BooleanSelector()
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_general",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders=self._info_placeholders(),
        )

    def _info_placeholders(self) -> dict[str, str]:
        config = self._common()
        return {
            "model": str(config.get("model", "?")),
            "firmware": str(config.get("ver", "?")),
            "hardware": str(config.get("hver", "?")),
            "battery": str(config.get("btPt", "?")),
        }

    # -- slots --------------------------------------------------------------

    async def async_step_beacon_slot_select(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose a slot and the advertisement type it should send."""
        if user_input is None:
            if (abort := await self._load()) is not None:
                return abort
        config = self._common()
        slots = {s["slot"]: s for s in config.get("advObj", [])}
        if user_input is not None:
            self._slot_index = int(user_input["slot"])
            self._slot_type = int(user_input["type"])
            return await self.async_step_beacon_slot()

        count = caps.slot_count(config)
        slot_options = [
            SelectOptionDict(
                value=str(i),
                label=f"Slot {i}: {caps.ADV_TYPES.get(slots.get(i, {}).get('type', 0), '?')}",
            )
            for i in range(count)
        ]
        current_type = slots.get(0, {}).get("type", 0)
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_slot_select",
            data_schema=vol.Schema(
                {
                    vol.Required("slot", default="0"): _select(slot_options),
                    vol.Required("type", default=str(current_type)): SelectSelector(
                        SelectSelectorConfig(
                            options=[str(t) for t in caps.supported_adv_types(config)],
                            translation_key="adv_type",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_beacon_slot(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Settings of one slot (fields depend on its type)."""
        config = self._common()
        index, adv_type = self._slot_index, self._slot_type
        current = next((s for s in config.get("advObj", []) if s["slot"] == index), {})
        same_type = current.get("type") == adv_type
        errors: dict[str, str] = {}

        if adv_type == 0:
            if (abort := await self._write({"advObj": [{"slot": index, "type": 0}]})) is not None:
                return abort
            return self._done()

        if user_input is not None:
            slot: dict[str, Any] = {
                "slot": index,
                "type": adv_type,
                "advPrd": float(user_input["advPrd"]),
                "txPwr": int(user_input["txPwr"]),
                "conn": 1 if user_input["conn"] else 0,
                "trAdv": 1 if user_input["trAdv"] else 0,
            }
            if "mode" in user_input:
                slot["mode"] = int(user_input["mode"])
            disabled = [int(c) for c in user_input.get("disabled_channels", [])]
            if len(disabled) == 3:
                errors["disabled_channels"] = "all_channels_disabled"
            slot["chMsk"] = sum(caps.CHANNELS[c] for c in disabled)
            if adv_type == 5:
                try:
                    slot["uuid"] = str(uuid.UUID(user_input["uuid"])).upper()
                except ValueError:
                    errors["uuid"] = "invalid_uuid"
                slot["majorID"] = int(user_input["majorID"])
                slot["minorID"] = int(user_input["minorID"])
            elif adv_type == 2:
                slot["nid"] = user_input["nid"].strip()
                slot["sid"] = user_input["sid"].strip()
            elif adv_type == 4:
                slot["url"] = user_input["url"].strip()
            if not errors:
                old_uuid = current.get("uuid")
                if (abort := await self._write({"advObj": [slot]})) is not None:
                    return abort
                return self._after_slot_write(old_uuid, slot.get("uuid"))

        defaults = current if same_type else {}
        ble5 = caps.supports_ble5(config)
        fields: dict[Any, Any] = {
            vol.Required("advPrd", default=defaults.get("advPrd", 1000)): _number(
                caps.MIN_ADV_PERIOD, caps.max_adv_period(config), "any", "ms"
            ),
            vol.Required("txPwr", default=str(defaults.get("txPwr", 0))): _select(
                {p: f"{p} dBm" for p in caps.tx_powers(config)}
            ),
            vol.Required("conn", default=bool(defaults.get("conn", 1))): BooleanSelector(),
            vol.Required("trAdv", default=bool(defaults.get("trAdv", 0))): BooleanSelector(),
        }
        if ble5:
            fields[vol.Required("mode", default=str(defaults.get("mode", 0)))] = SelectSelector(
                SelectSelectorConfig(
                    options=[str(m) for m in caps.ADV_MODES],
                    translation_key="adv_mode",
                    mode=SelectSelectorMode.DROPDOWN,
                )
            )
        mask = int(defaults.get("chMsk", 0))
        fields[
            vol.Optional(
                "disabled_channels",
                default=[str(c) for c, bit in caps.CHANNELS.items() if mask & bit],
            )
        ] = _select({c: f"Channel {c}" for c in caps.CHANNELS}, multiple=True)
        if adv_type == 5:
            fields[vol.Required("uuid", default=defaults.get("uuid", current.get("uuid", "")))] = TextSelector()
            fields[vol.Required("majorID", default=defaults.get("majorID", 1))] = _number(0, 65535)
            fields[vol.Required("minorID", default=defaults.get("minorID", 1))] = _number(0, 65535)
        elif adv_type == 2:
            fields[vol.Required("nid", default=defaults.get("nid", "0x00000000000000000001"))] = TextSelector()
            fields[vol.Required("sid", default=defaults.get("sid", "0x000000000001"))] = TextSelector()
        elif adv_type == 4:
            fields[vol.Required("url", default=defaults.get("url", "https://"))] = TextSelector()

        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_slot",
            data_schema=vol.Schema(fields),
            errors=errors,
            description_placeholders={
                "slot": str(index),
                "type": caps.ADV_TYPES[adv_type],
            },
        )

    def _after_slot_write(self, old_uuid: str | None, new_uuid: str | None) -> SubentryFlowResult:
        """Keep the bin tracking the beacon when its idle UUID changed."""
        subentry = self._get_reconfigure_subentry()  # type: ignore[attr-defined]
        idle = subentry.data.get(CONF_IDLE_UUID, "")
        if (
            old_uuid and new_uuid
            and old_uuid.lower() == idle.lower()
            and new_uuid.lower() != idle.lower()
        ):
            data = {**subentry.data, CONF_IDLE_UUID: new_uuid.lower()}
            if data.get(CONF_MOTION_UUID) != MOTION_UUID_AUTO:
                data[CONF_MOTION_UUID] = MOTION_UUID_AUTO
            return self.async_update_and_abort(  # type: ignore[attr-defined]
                self._get_entry(), subentry, data=data  # type: ignore[attr-defined]
            )
        return self._done()

    # -- triggers -----------------------------------------------------------

    async def async_step_beacon_trigger_select(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose a trigger and its type."""
        if user_input is None:
            if (abort := await self._load()) is not None:
                return abort
        config = self._common()
        triggers = {t["trIdx"]: t for t in config.get("trObj", [])}
        if user_input is not None:
            self._trigger_index = int(user_input["trigger"])
            self._trigger_type = int(user_input["type"])
            return await self.async_step_beacon_trigger()
        options = [
            SelectOptionDict(
                value=str(i),
                label=f"Trigger {i}: {caps.TRIGGER_TYPES.get(triggers.get(i, {}).get('trType', 0), '?')}",
            )
            for i in range(caps.trigger_count(config))
        ]
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_trigger_select",
            data_schema=vol.Schema(
                {
                    vol.Required("trigger", default="0"): _select(options),
                    vol.Required(
                        "type", default=str(triggers.get(0, {}).get("trType", 0))
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[str(t) for t in caps.supported_trigger_types(config)],
                            translation_key="trigger_type",
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_beacon_trigger(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Settings of one trigger."""
        config = self._common()
        index, trigger_type = self._trigger_index, self._trigger_type
        current = next((t for t in config.get("trObj", []) if t["trIdx"] == index), {})
        defaults = current if current.get("trType") == trigger_type else {}

        if trigger_type == 0:
            if (abort := await self._write({"trObj": [{"trIdx": index, "trType": 0}]})) is not None:
                return abort
            return self._done()

        if user_input is not None:
            trigger: dict[str, Any] = {
                "trIdx": index,
                "trType": trigger_type,
                "trAct": sum(int(a) for a in user_input["trAct"]) or 0,
                "slot": int(user_input["slot"]),
                "trAChg": 1 if user_input["trAChg"] else 0,
                "trATm": int(user_input["trATm"]),
                "trAPrd": float(user_input["trAPrd"]),
                "trAPwr": int(user_input["trAPwr"]),
            }
            if trigger_type in caps.MOTION_TRIGGERS:
                trigger["trPara"] = int(user_input["trPara"])
                trigger["odr"] = int(user_input["odr"])
                trigger["ocnt"] = int(user_input["ocnt"])
            elif "trPara" in user_input:
                trigger["trPara"] = int(user_input["trPara"])
            if (abort := await self._write({"trObj": [trigger]})) is not None:
                return abort
            return self._done()

        actions = int(defaults.get("trAct", 1))
        fields: dict[Any, Any] = {
            vol.Required(
                "trAct", default=[str(a) for a in caps.TRIGGER_ACTIONS if actions & a]
            ): SelectSelector(
                SelectSelectorConfig(
                    options=[str(a) for a in caps.TRIGGER_ACTIONS],
                    translation_key="trigger_action",
                    multiple=True,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required("slot", default=str(defaults.get("slot", 0))): _select(
                {i: f"Slot {i}" for i in range(caps.slot_count(config))}
            ),
            vol.Required("trAChg", default=bool(defaults.get("trAChg", 1))): BooleanSelector(),
            vol.Required("trATm", default=defaults.get("trATm", 30)): _number(
                1, caps.MAX_TRIGGER_ADV_TIME, 1, "s"
            ),
            vol.Required("trAPrd", default=defaults.get("trAPrd", 400)): _number(
                caps.MIN_ADV_PERIOD, caps.max_adv_period(config), "any", "ms"
            ),
            vol.Required("trAPwr", default=str(defaults.get("trAPwr", 0))): _select(
                {p: f"{p} dBm" for p in caps.tx_powers(config)}
            ),
        }
        if trigger_type in caps.MOTION_TRIGGERS:
            fields[vol.Required("trPara", default=defaults.get("trPara", 2))] = _number(
                caps.MIN_SENSITIVITY, caps.MAX_SENSITIVITY, 1
            )
            fields[vol.Required("odr", default=str(defaults.get("odr", 2)))] = _select(caps.ACC_ODR)
            fields[vol.Required("ocnt", default=defaults.get("ocnt", 1))] = _number(1, 255, 1)
        elif trigger_type == 20:
            fields[vol.Required("trPara", default=defaults.get("trPara", 60000))] = _number(
                100, 43200000, 100, "ms"
            )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_trigger",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "trigger": str(index),
                "type": caps.TRIGGER_TYPES[trigger_type],
            },
        )

    # -- export / import ----------------------------------------------------

    async def async_step_beacon_export(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Show a settings template to copy."""
        if user_input is not None:
            return self.async_abort(reason="export_done")  # type: ignore[attr-defined]
        if (abort := await self._load()) is not None:
            return abort
        template = json.dumps(export_settings(self._common()), separators=(",", ":"))
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_export",
            data_schema=vol.Schema(
                {
                    vol.Optional("template", default=template): TextSelector(
                        TextSelectorConfig(multiline=True, read_only=True)
                    )
                }
            ),
        )

    async def async_step_beacon_import(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Paste a template; the beacon keeps its name, password and UUIDs."""
        errors: dict[str, str] = {}
        placeholders = {"error": ""}
        if user_input is not None:
            try:
                template = json.loads(user_input["template"])
                if isinstance(template, dict) and "settings" in template:
                    template = template["settings"]  # pasted a service response
            except json.JSONDecodeError:
                errors["template"] = "invalid_json"
            else:
                found = self._hub_and_bin()
                if found is None:
                    return self.async_abort(reason="not_loaded")  # type: ignore[attr-defined]
                hub, bin_ = found
                try:
                    await hub.beacons.async_import(
                        bin_, template, user_input["allow_other_model"]
                    )
                except BeaconImportError as err:
                    errors["template"] = "import_rejected"
                    placeholders["error"] = str(err)
                except BeaconError as err:
                    return self.async_abort(  # type: ignore[attr-defined]
                        reason="beacon_error", description_placeholders={"error": str(err)}
                    )
                else:
                    return self._done()
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_import",
            data_schema=vol.Schema(
                {
                    vol.Required("template"): TextSelector(TextSelectorConfig(multiline=True)),
                    vol.Required("allow_other_model", default=False): BooleanSelector(),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    # -- power profiler -----------------------------------------------------

    async def async_step_beacon_power(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Estimated battery life from the beacon's current settings."""
        subentry = self._get_reconfigure_subentry()  # type: ignore[attr-defined]
        if user_input is not None:
            capacity = float(user_input[CONF_BATTERY_CAPACITY])
            return self.async_update_and_abort(  # type: ignore[attr-defined]
                self._get_entry(), subentry,  # type: ignore[attr-defined]
                data={**subentry.data, CONF_BATTERY_CAPACITY: capacity},
            )
        if (abort := await self._load()) is not None:
            return abort
        hub, bin_ = self._hub_and_bin()  # type: ignore[misc]
        result = power_estimate(self._common(), bin_.capacity_mah)
        lines = []
        for slot in result["slots"]:
            what = "only when triggered" if slot["trigger_only"] else (
                f"{slot['interval_ms']:g} ms at {slot['tx_power']} dBm"
                f"{', connectable' if slot['connectable'] else ''}"
            )
            lines.append(f"- Slot {slot['slot']}: {what} → {slot['current_ua']} µA")
        lines.append(f"- Sleep: {result['sleep_ua']} µA")
        if result["accelerometer_ua"]:
            lines.append(f"- Accelerometer (motion trigger): {result['accelerometer_ua']} µA")
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_power",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERY_CAPACITY, default=bin_.capacity_mah
                    ): _number(10, 20000, 10, "mAh"),
                }
            ),
            description_placeholders={
                "current": f"{result['average_current_ua']}",
                "days": f"{result['life_days']}",
                "years": f"{result['life_years']}",
                "breakdown": "\n".join(lines),
                "battery": str(self._common().get("btPt", "?")),
            },
        )

    # -- actions ------------------------------------------------------------

    async def async_step_beacon_identify(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Flash the LED."""
        found = self._hub_and_bin()
        if found is None:
            return self.async_abort(reason="not_loaded")  # type: ignore[attr-defined]
        hub, bin_ = found
        try:
            await hub.beacons.async_identify(bin_)
        except BeaconError as err:
            return self.async_abort(  # type: ignore[attr-defined]
                reason="beacon_error", description_placeholders={"error": str(err)}
            )
        return self.async_abort(reason="identify_done")  # type: ignore[attr-defined]

    async def async_step_beacon_password(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Set a new beacon password and store it in the bin."""
        errors: dict[str, str] = {}
        if user_input is not None:
            new = user_input["new_password"]
            try:
                validate_password(new)
            except ValueError:
                errors["new_password"] = "password_length"
            if not errors and new != user_input["confirm_password"]:
                errors["confirm_password"] = "password_mismatch"
            if not errors:
                found = self._hub_and_bin()
                if found is None:
                    return self.async_abort(reason="not_loaded")  # type: ignore[attr-defined]
                hub, bin_ = found
                subentry = self._get_reconfigure_subentry()  # type: ignore[attr-defined]
                try:
                    await hub.beacons.async_change_password(bin_, new)
                except BeaconError as err:
                    if bin_.password == new:
                        # The beacon took it; keep it even though verifying failed.
                        hub.hass.config_entries.async_update_subentry(
                            self._get_entry(), subentry,  # type: ignore[attr-defined]
                            data={**subentry.data, CONF_BEACON_PASSWORD: new},
                        )
                    return self.async_abort(  # type: ignore[attr-defined]
                        reason="beacon_error", description_placeholders={"error": str(err)}
                    )
                return self.async_update_and_abort(  # type: ignore[attr-defined]
                    self._get_entry(), subentry,  # type: ignore[attr-defined]
                    data={**subentry.data, CONF_BEACON_PASSWORD: new},
                )
        password = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="beacon_password",
            data_schema=vol.Schema(
                {
                    vol.Required("new_password"): password,
                    vol.Required("confirm_password"): password,
                }
            ),
            errors=errors,
        )

    async def async_step_beacon_reset(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Factory-reset the beacon (confirmation required)."""
        if user_input is None or not user_input.get("confirm"):
            return self.async_show_form(  # type: ignore[attr-defined]
                step_id="beacon_reset",
                data_schema=vol.Schema({vol.Required("confirm", default=False): BooleanSelector()}),
                errors={"confirm": "confirm_required"} if user_input is not None else {},
            )
        found = self._hub_and_bin()
        if found is None:
            return self.async_abort(reason="not_loaded")  # type: ignore[attr-defined]
        hub, bin_ = found
        try:
            await hub.beacons.async_reset(bin_)
        except BeaconError as err:
            return self.async_abort(  # type: ignore[attr-defined]
                reason="beacon_error", description_placeholders={"error": str(err)}
            )
        subentry = self._get_reconfigure_subentry()  # type: ignore[attr-defined]
        # A reset restores KKM's default password; the UUIDs change too, so the
        # bin needs its idle UUID updated (Bin settings) once it advertises again.
        return self.async_update_and_abort(  # type: ignore[attr-defined]
            self._get_entry(), subentry,  # type: ignore[attr-defined]
            data={**subentry.data, CONF_BEACON_PASSWORD: DEFAULT_PASSWORD},
        )

    async def async_step_beacon_power_off(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Power the beacon off (confirmation required)."""
        if user_input is None or not user_input.get("confirm"):
            return self.async_show_form(  # type: ignore[attr-defined]
                step_id="beacon_power_off",
                data_schema=vol.Schema({vol.Required("confirm", default=False): BooleanSelector()}),
                errors={"confirm": "confirm_required"} if user_input is not None else {},
            )
        found = self._hub_and_bin()
        if found is None:
            return self.async_abort(reason="not_loaded")  # type: ignore[attr-defined]
        hub, bin_ = found
        try:
            await hub.beacons.async_power_off(bin_)
        except BeaconError as err:
            return self.async_abort(  # type: ignore[attr-defined]
                reason="beacon_error", description_placeholders={"error": str(err)}
            )
        return self.async_abort(reason="power_off_done")  # type: ignore[attr-defined]
