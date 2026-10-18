"""Battery-life estimate for a KBeacon (pure), like KBeacon Pro's power profiler.

KKM's app computes this from the configuration (it is not measured by the
beacon), counting only regular advertising: triggered broadcasts and time
spent connected are left out, and the battery is assumed to deliver its
ideal capacity. This model follows the same rules with figures from Nordic's
nRF52 datasheets (KKM beacons are nRF52 based, ~3 V coin cell, DC/DC on):

* one legacy advertising event = 3 channels x (TX of a 31-byte PDU at the
  configured power) + radio ramp-up and CPU wake-up;
* connectable advertising listens after each channel (ADV_IND), which costs
  roughly 10-20 % more, matching KKM's own guidance;
* sleep current, plus the accelerometer when a motion trigger is enabled.

It is an estimate: real coin cells deliver less in the cold and under pulses.
"""

from __future__ import annotations

from typing import Any

DEFAULT_CAPACITY_MAH = 1000  # CR2477, the cell most KKM card/tag beacons use

# nRF52 radio TX current (mA) by output power (dBm), DC/DC enabled.
_TX_CURRENT_MA = {
    -40: 2.3, -20: 3.2, -16: 3.3, -12: 3.5, -8: 3.8, -4: 4.2, 0: 5.3,
    3: 6.4, 4: 7.0, 5: 8.0, 6: 9.0, 7: 10.0, 8: 11.0,
}
_TX_TIME_MS = 0.4  # one 31-byte legacy advertising PDU incl. preamble/CRC
_RX_CURRENT_MA = 5.4
_RX_WINDOW_MS = 0.1  # listening for a connect/scan request after each TX
_EVENT_OVERHEAD_UC = 3.0  # HFXO start, radio ramp-up, CPU wake per event
_SLEEP_UA = 2.5
# Accelerometer current (uA) by output data rate setting (motion trigger "odr").
_ACC_UA = {0: 2.0, 1: 3.0, 2: 6.0, 3: 11.0}
_DEFAULT_ACC_UA = 6.0

TYPE_DISABLED = 0
MOTION_TRIGGER = 1


def _tx_current(power: int) -> float:
    if power in _TX_CURRENT_MA:
        return _TX_CURRENT_MA[power]
    nearest = min(_TX_CURRENT_MA, key=lambda p: abs(p - power))
    return _TX_CURRENT_MA[nearest]


def event_charge_uc(tx_power: int, connectable: bool) -> float:
    """Charge of one advertising event on the three channels (microcoulombs)."""
    charge = 3 * _tx_current(tx_power) * _TX_TIME_MS + _EVENT_OVERHEAD_UC
    if connectable:
        charge += 3 * _RX_CURRENT_MA * _RX_WINDOW_MS
    return charge


def estimate(config: dict[str, Any], capacity_mah: float = DEFAULT_CAPACITY_MAH) -> dict[str, Any]:
    """Return the average current and battery life for a beacon configuration."""
    slots: list[dict[str, Any]] = []
    total_ua = _SLEEP_UA
    for slot in config.get("advObj", []):
        if slot.get("type", TYPE_DISABLED) == TYPE_DISABLED:
            continue
        interval_ms = float(slot.get("advPrd") or 1000)
        trigger_only = bool(slot.get("trAdv"))
        current = 0.0
        if not trigger_only:
            current = event_charge_uc(int(slot.get("txPwr", 0)), bool(slot.get("conn"))) / (
                interval_ms / 1000
            )
        total_ua += current
        slots.append(
            {
                "slot": slot.get("slot"),
                "type": slot.get("type"),
                "interval_ms": interval_ms,
                "tx_power": slot.get("txPwr"),
                "connectable": bool(slot.get("conn")),
                "trigger_only": trigger_only,
                "current_ua": round(current, 2),
            }
        )

    sensor_ua = 0.0
    for trigger in config.get("trObj", []):
        if trigger.get("trType") == MOTION_TRIGGER:
            sensor_ua = max(sensor_ua, _ACC_UA.get(int(trigger.get("odr", 2)), _DEFAULT_ACC_UA))
    total_ua += sensor_ua

    hours = capacity_mah * 1000 / total_ua
    return {
        "average_current_ua": round(total_ua, 1),
        "sleep_ua": _SLEEP_UA,
        "accelerometer_ua": sensor_ua,
        "slots": slots,
        "capacity_mah": capacity_mah,
        "life_days": round(hours / 24),
        "life_years": round(hours / 24 / 365, 1),
    }
