"""Sensors for Irabelle Waste Management."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfTime,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import WasteConfigEntry
from .const import DOMAIN, STATUS_AWAITING, STATUS_EMPTIED, STATUSES
from .entity import BinEntity, HubEntity
from .hub import Bin, WasteHub


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WasteConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the bin and hub sensors."""
    hub = entry.runtime_data
    async_add_entities(
        [
            BinsToTakeOutSensor(hub),
            BinsByStatusSensor(hub, "bins_awaiting_collection", STATUS_AWAITING),
            BinsByStatusSensor(hub, "bins_to_bring_back", STATUS_EMPTIED),
        ]
    )
    for bin_ in hub.bins.values():
        entities: list[SensorEntity] = [
            BinStatusSensor(hub, bin_),
            BinLastMotionSensor(hub, bin_),
            BinLastSeenSensor(hub, bin_),
            BinSignalSensor(hub, bin_),
            BinBatterySensor(hub, bin_),
            BinBatteryLifeSensor(hub, bin_),
        ]
        if bin_.calendar:
            entities.append(BinNextCollectionSensor(hub, bin_))
        async_add_entities(entities, config_subentry_id=bin_.subentry_id)


class BinStatusSensor(BinEntity, SensorEntity):
    """Filling / awaiting collection / emptied."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = STATUSES

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the status sensor."""
        super().__init__(hub, bin_, "status")

    @property
    def native_value(self) -> str | None:
        """Return the bin status."""
        return self.bin.tracker.status

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose when and why the status last changed."""
        tracker = self.bin.tracker
        return {
            "status_since": tracker.status_since,
            "status_reason": tracker.status_reason,
            "next_collection": self.bin.next_collection,
            "due": self.hub.due(self.bin),
        }


class BinLastMotionSensor(BinEntity, SensorEntity):
    """When the beacon last advertised motion."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the sensor."""
        super().__init__(hub, bin_, "last_motion")

    @property
    def native_value(self) -> datetime | None:
        """Return the last motion time."""
        return self.bin.tracker.last_motion


class BinLastSeenSensor(BinEntity, SensorEntity):
    """When the beacon last sent an idle (normal) advertisement."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the sensor."""
        super().__init__(hub, bin_, "last_seen")

    @property
    def native_value(self) -> datetime | None:
        """Return the last idle advertisement time (minute resolution)."""
        last = self.bin.tracker.last_seen
        # Rounded so this does not write a new state for every advertisement.
        return last.replace(second=0, microsecond=0) if last else None


class BinSignalSensor(BinEntity, SensorEntity):
    """Strongest recent idle-frame RSSI across all proxies."""

    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = SIGNAL_STRENGTH_DECIBELS_MILLIWATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the sensor."""
        super().__init__(hub, bin_, "signal")

    @property
    def native_value(self) -> int | None:
        """Return the RSSI, or None when no idle frame was heard recently."""
        return self.bin.signal


class BinBatterySensor(BinEntity, SensorEntity):
    """Battery level from the KKM extension data (scan response)."""

    _attr_device_class = SensorDeviceClass.BATTERY
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the sensor."""
        super().__init__(hub, bin_, "battery")

    @property
    def native_value(self) -> int | None:
        """Return the battery percent (None until a scan response is heard)."""
        return self.bin.tracker.battery()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the unclamped value the beacon reports."""
        return {"reported_level": self.bin.tracker.battery_reported}


class BinBatteryLifeSensor(BinEntity, SensorEntity):
    """Estimated remaining battery life (like KBeacon Pro's power profiler)."""

    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.DAYS
    _attr_suggested_display_precision = 0
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the sensor."""
        super().__init__(hub, bin_, "battery_life")

    def _estimate(self) -> dict[str, Any] | None:
        return self.hub.beacons.power_estimate(self.bin)

    @property
    def native_value(self) -> float | None:
        """Days left: full-battery life scaled by the battery level."""
        if (estimate := self._estimate()) is None:
            return None
        level = self.bin.tracker.battery()
        return round(estimate["life_days"] * (level if level is not None else 100) / 100)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Average current and the assumptions behind the estimate."""
        if (estimate := self._estimate()) is None:
            return {}
        return {
            "average_current_ua": estimate["average_current_ua"],
            "full_battery_life_months": estimate["life_months"],
            "battery_capacity_mah": estimate["capacity_mah"],
            "battery_efficiency": estimate["efficiency"],
            "standby_ua": estimate["standby_ua"],
            "accelerometer_standby_ua": estimate["accelerometer_standby_ua"],
            "motion_sampling_ua": estimate["motion_sampling_ua"],
            "slots": estimate["slots"],
            "triggered_advertising": estimate["triggered_advertising"],
        }


class BinNextCollectionSensor(BinEntity, SensorEntity):
    """Next collection date from the bin's calendar."""

    _attr_device_class = SensorDeviceClass.DATE

    def __init__(self, hub: WasteHub, bin_: Bin) -> None:
        """Initialise the sensor."""
        super().__init__(hub, bin_, "next_collection")

    @property
    def native_value(self) -> date | None:
        """Return the next collection date."""
        return self.bin.next_collection

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose whether it is due today or tomorrow."""
        return {"due": self.hub.due(self.bin)}


def _summary_attributes(hub: WasteHub, bins: list[Bin]) -> dict[str, Any]:
    names = [b.name for b in bins]
    return {
        "bins": names,
        "sentence": _join_names(names),
        "status_entities": _status_entity_ids(hub, bins),
    }


def _join_names(names: list[str]) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} and {names[-1]}"


def _status_entity_ids(hub: WasteHub, bins: list[Bin]) -> list[str]:
    registry = er.async_get(hub.hass)
    return [
        entity_id
        for b in bins
        if (
            entity_id := registry.async_get_entity_id(
                "sensor", DOMAIN, f"{b.subentry_id}_status"
            )
        )
    ]


class BinsToTakeOutSensor(HubEntity, SensorEntity):
    """Bins due today or tomorrow that are still filling (at home)."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: WasteHub) -> None:
        """Initialise the sensor."""
        super().__init__(hub, "bins_to_take_out")

    @property
    def native_value(self) -> int:
        """Return how many bins need to go out."""
        return len(self.hub.bins_to_take_out())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the bins and when each is due."""
        bins = self.hub.bins_to_take_out()
        return {
            **_summary_attributes(self.hub, bins),
            "due": {b.name: self.hub.due(b) for b in bins},
        }


class BinsByStatusSensor(HubEntity, SensorEntity):
    """How many bins are in a given status, and which."""

    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: WasteHub, key: str, status: str) -> None:
        """Initialise the sensor."""
        super().__init__(hub, key)
        self._status = status

    @property
    def native_value(self) -> int:
        """Return the number of bins."""
        return len(self.hub.bins_with_status(self._status))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """List the bins."""
        return _summary_attributes(self.hub, self.hub.bins_with_status(self._status))
