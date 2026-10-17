"""What a KBeacon supports, decoded from its common settings (pure).

Bit layouts follow KKM's SDK (KBCfgCommon.java): ``bCap`` bits 8-15 are the
advertisement types (bit ``type - 1``), bits 16+ the BLE 5 features;
``trCap`` bit ``trigger_type - 1`` is set for every supported trigger.
"""

from __future__ import annotations

from typing import Any

# Advertisement (slot) types
ADV_TYPES: dict[int, str] = {
    0: "disabled",
    1: "ksensor",
    2: "eddystone_uid",
    3: "eddystone_tlm",
    4: "eddystone_url",
    5: "ibeacon",
    6: "system",
    7: "aoa",
    8: "ebeacon",
}

# Trigger types
TRIGGER_TYPES: dict[int, str] = {
    0: "disabled",
    1: "motion",
    2: "angle",
    3: "button_long_press",
    4: "button_single_click",
    5: "button_double_click",
    6: "button_triple_click",
    7: "cutoff",
    8: "temperature_above",
    9: "temperature_below",
    10: "humidity_above",
    11: "humidity_below",
    12: "humidity_periodically",
    13: "pir_detected",
    14: "light_above",
    15: "light_below",
    20: "periodically",
}
MOTION_TRIGGERS = {1}
BUTTON_TRIGGERS = {3, 4, 5, 6}

# Trigger action flags (trAct)
TRIGGER_ACTIONS: dict[int, str] = {
    0x1: "advertise",
    0x2: "alert",
    0x4: "record",
    0x8: "vibrate",
    0x10: "report_to_app",
    0x20: "ble_scan",
}

ADV_MODES: dict[int, str] = {0: "legacy", 1: "long_range", 2: "extended"}

# Accelerometer output data rate (trigger "odr")
ACC_ODR: dict[int, str] = {0: "1 Hz", 1: "10 Hz", 2: "25 Hz", 3: "50 Hz"}

TX_POWERS = [-40, -20, -16, -12, -8, -4, 0, 3, 4, 5, 6, 7, 8]

# Channel mask bits (chMsk): set = channel disabled
CHANNELS = {37: 0x4, 38: 0x2, 39: 0x1}

MIN_ADV_PERIOD = 100.0
MAX_TRIGGER_ADV_TIME = 7200
MIN_SENSITIVITY, MAX_SENSITIVITY = 1, 126


def supported_adv_types(common: dict[str, Any]) -> list[int]:
    """Advertisement types this beacon can use (always includes disabled)."""
    cap = (int(common.get("bCap", 0)) >> 8) & 0xFF
    types = [t for t in ADV_TYPES if t and (cap >> (t - 1)) & 1]
    return [0, *types] if types else list(ADV_TYPES)


def supported_trigger_types(common: dict[str, Any]) -> list[int]:
    """Trigger types this beacon supports (always includes disabled)."""
    cap = int(common.get("trCap", 0))
    types = [t for t in TRIGGER_TYPES if t and (cap >> (t - 1)) & 1]
    return [0, *types]


def supports_ble5(common: dict[str, Any]) -> bool:
    """True when the beacon offers long-range / extended advertising."""
    return bool((int(common.get("bCap", 0)) >> 16) & 0x6)


def tx_powers(common: dict[str, Any]) -> list[int]:
    """Allowed TX powers between the beacon's min and max."""
    low = int(common.get("minPwr", TX_POWERS[0]))
    high = int(common.get("maxPwr", TX_POWERS[-1]))
    return [p for p in TX_POWERS if low <= p <= high]


def max_adv_period(common: dict[str, Any]) -> float:
    """Longest advertising interval the beacon accepts (ms)."""
    return float(common.get("maxPrd", 20000))


def slot_count(common: dict[str, Any]) -> int:
    """Number of advertisement slots."""
    return int(common.get("maxSlot", 5))


def trigger_count(common: dict[str, Any]) -> int:
    """Number of triggers."""
    return int(common.get("maxTg", 5))
