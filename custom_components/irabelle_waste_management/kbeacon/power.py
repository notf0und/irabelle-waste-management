"""Battery-life estimate for a KBeacon (pure), matching KBeacon Pro's power profiler.

KKM's app computes this from the configuration (it is not measured by the
beacon): average current = advertising standby + every regular advertising
slot + accelerometer standby + the motion trigger's sampling. Triggered
advertising is shown but not included, and

    battery life (months) = capacity (mAh) x efficiency / average (mA) / 24 / 30

with an efficiency of 75 % recommended by KKM. The per-event figures below
are calibrated on KBeacon Pro's own numbers for a K4P (standby 3.0 uA; 1 s
iBeacon at -16 dBm connectable = 11.0 uA; 1 s at +8 dBm = 25.5 uA; motion
sampling at 25 Hz = 3.0 uA; accelerometer standby 0.1 uA); the shape across
transmit powers follows Nordic's nRF52 radio datasheets:

* one legacy advertising event = 3 channels x (TX of a 31-byte PDU at the
  configured power) + radio ramp-up and CPU wake-up;
* connectable advertising listens after each channel (ADV_IND), which costs
  roughly 10-20 % more, matching KKM's own guidance;
* sleep current, plus the accelerometer when a motion trigger is enabled.

It is an estimate: real coin cells deliver less in the cold and under pulses.
"""

from __future__ import annotations

from typing import Any

DEFAULT_CAPACITY_MAH = 1000  # KBeacon Pro's default (CR2477)
DEFAULT_EFFICIENCY = 75  # percent, KKM's recommendation

# Radio TX current (mA) by output power (dBm).
_TX_CURRENT_MA = {
    -40: 3.0, -20: 3.6, -16: 3.8, -12: 4.2, -8: 4.7, -4: 5.4, 0: 6.4,
    3: 8.0, 4: 9.0, 5: 10.5, 6: 12.0, 7: 13.9, 8: 15.9,
}
_TX_TIME_MS = 0.4  # one 31-byte legacy advertising PDU incl. preamble/CRC
_RX_CURRENT_MA = 5.4
_RX_WINDOW_MS = 0.1  # listening for a connect/scan request after each TX
_EVENT_OVERHEAD_UC = 4.82  # HFXO start, radio ramp-up, CPU wake per event
_STANDBY_UA = 3.0
_ACC_STANDBY_UA = 0.1
# Motion sampling current (uA) by accelerometer output data rate ("odr"):
# 25 Hz is KBeacon Pro's figure, the others scale like an LIS2DH12.
_ACC_MOTION_UA = {0: 1.5, 1: 2.25, 2: 3.0, 3: 4.5}
_DEFAULT_ACC_MOTION_UA = 3.0

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


def _slot_current(interval_ms: float, tx_power: int, connectable: bool) -> float:
    return event_charge_uc(tx_power, connectable) / (interval_ms / 1000)


def estimate(
    config: dict[str, Any],
    capacity_mah: float = DEFAULT_CAPACITY_MAH,
    efficiency: float = DEFAULT_EFFICIENCY,
) -> dict[str, Any]:
    """Return KBeacon Pro's power-profiler figures for a configuration."""
    slots_by_index = {s.get("slot"): s for s in config.get("advObj", [])}
    slots: list[dict[str, Any]] = []
    total_ua = _STANDBY_UA
    for slot in config.get("advObj", []):
        if slot.get("type", TYPE_DISABLED) == TYPE_DISABLED or slot.get("trAdv"):
            continue  # disabled, or only advertises when triggered
        interval_ms = float(slot.get("advPrd") or 1000)
        current = _slot_current(interval_ms, int(slot.get("txPwr", 0)), bool(slot.get("conn")))
        total_ua += current
        slots.append(
            {
                "slot": slot.get("slot"),
                "type": slot.get("type"),
                "interval_ms": interval_ms,
                "tx_power": slot.get("txPwr"),
                "connectable": bool(slot.get("conn")),
                "current_ua": round(current, 2),
            }
        )

    accelerometer_ua = 0.0
    motion_ua = 0.0
    triggers: list[dict[str, Any]] = []
    for trigger in config.get("trObj", []):
        if trigger.get("trType", 0) == 0:
            continue
        if trigger.get("trType") == MOTION_TRIGGER:
            accelerometer_ua = _ACC_STANDBY_UA
            motion_ua = max(
                motion_ua,
                _ACC_MOTION_UA.get(int(trigger.get("odr", 2)), _DEFAULT_ACC_MOTION_UA),
            )
        if int(trigger.get("trAct", 0)) & 0x1:  # advertises when triggered
            slot = slots_by_index.get(trigger.get("slot"), {})
            triggers.append(
                {
                    "trigger": trigger.get("trIdx"),
                    "type": trigger.get("trType"),
                    "slot": trigger.get("slot"),
                    "current_ua": round(
                        _slot_current(
                            float(trigger.get("trAPrd") or 1000),
                            int(trigger.get("trAPwr", 0)),
                            bool(slot.get("conn")),
                        ),
                        2,
                    ),
                }
            )
    total_ua += accelerometer_ua + motion_ua

    months = capacity_mah * (efficiency / 100) / (total_ua / 1000) / 24 / 30
    return {
        "average_current_ua": round(total_ua, 1),
        "standby_ua": _STANDBY_UA,
        "accelerometer_standby_ua": accelerometer_ua,
        "motion_sampling_ua": motion_ua,
        "slots": slots,
        "triggered_advertising": triggers,
        "capacity_mah": capacity_mah,
        "efficiency": efficiency,
        "life_months": round(months, 1),
        "life_days": round(months * 30),
    }
