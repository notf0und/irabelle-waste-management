"""Constants for Irabelle Waste Management."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "irabelle_waste_management"

SUBENTRY_BIN: Final = "bin"

# Hub (config entry) options
CONF_WINDOW_START: Final = "collection_window_start"
CONF_WINDOW_END: Final = "collection_window_end"
CONF_AWAY_MINUTES: Final = "away_minutes"
CONF_RETURN_SECONDS: Final = "return_confirm_seconds"

DEFAULT_WINDOW_START: Final = "05:00:00"
DEFAULT_WINDOW_END: Final = "19:00:00"
DEFAULT_AWAY_MINUTES: Final = 10
DEFAULT_RETURN_SECONDS: Final = 120

# Bin (subentry) data
CONF_ADDRESS: Final = "address"
CONF_IDLE_UUID: Final = "idle_uuid"
CONF_MOTION_UUID: Final = "motion_uuid"
CONF_CALENDAR: Final = "calendar_entity"
CONF_KEYWORD: Final = "calendar_keyword"
CONF_BEACON_PASSWORD: Final = "beacon_password"
CONF_BATTERY_CAPACITY: Final = "battery_capacity"

# Any iBeacon frame from the bin's MAC whose UUID is not the idle UUID.
MOTION_UUID_AUTO: Final = "auto"

# Bin statuses
STATUS_FILLING: Final = "filling"
STATUS_AWAITING: Final = "awaiting_collection"
STATUS_EMPTIED: Final = "emptied"
STATUSES: Final = [STATUS_FILLING, STATUS_AWAITING, STATUS_EMPTIED]

# Tuning that is not worth exposing in the UI
TICK_SECONDS: Final = 15
# Gap between idle frames that breaks a "constantly detected" streak.
IDLE_STREAK_GAP_SECONDS: Final = 60
# The bin counts as "in range" while an idle frame arrived this recently.
IN_RANGE_SECONDS: Final = 120
# The motion binary sensor stays on this long after the last motion frame.
MOTION_HOLD_SECONDS: Final = 30
# How often the signal sensor is allowed to change (keeps the recorder quiet).
SIGNAL_UPDATE_SECONDS: Final = 60
# RSSI readings older than this are ignored when picking the best proxy.
SIGNAL_WINDOW_SECONDS: Final = 60
CALENDAR_REFRESH_MINUTES: Final = 30
# Proxies scan passively by default, so the scan response carrying the battery
# (KKM service data 0x2080) is only heard during a short active sweep.
BATTERY_SWEEP_HOURS: Final = 6
BATTERY_SWEEP_SECONDS: Final = 10
# Sooner retry while a bin has no battery reading yet (weak or new beacons).
BATTERY_RETRY_MINUTES: Final = 30
CALENDAR_LOOKAHEAD_DAYS: Final = 45

STORAGE_VERSION: Final = 1
SAVE_DELAY_SECONDS: Final = 10

SIGNAL_HUB_UPDATED: Final = f"{DOMAIN}_hub_updated_{{}}"
SIGNAL_BIN_UPDATED: Final = f"{DOMAIN}_bin_updated_{{}}"
