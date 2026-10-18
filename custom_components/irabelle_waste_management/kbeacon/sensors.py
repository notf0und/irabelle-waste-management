"""KBeacon sensor settings, sensor-trigger parameters and KSensor content (pure).

Field names, units and limits come from KKM's SDK (KBCfgSensor*.java,
KBCfgTrigger*.java, KBCfgAdvKSensor.java).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone, tzinfo
from typing import Any

# Sensor types (srType)
SENSOR_TYPES: dict[int, str] = {
    0x1: "accelerometer",
    0x2: "temperature_humidity",
    0x8: "alarm",
    0x10: "pir",
    0x20: "light",
    0x40: "voc",
    0x41: "co2",
    0x42: "geomagnetic",
    0x44: "scan",
    0x45: "nfc",
}


@dataclass(frozen=True, slots=True)
class Field:
    """One numeric/boolean setting of a sensor or trigger."""

    key: str
    kind: str  # "int", "float", "bool", "select"
    minimum: float = 0
    maximum: float = 0
    step: float = 1
    unit: str | None = None
    scale: float = 1  # beacon value = UI value * scale (e.g. 0.1 °C units)
    default: Any = None
    options: dict[int, str] | None = None


_LOG = Field("log", "bool", default=False)
_LOG_INTERVAL = Field("lgItvl", "int", 1, 14400, 1, "s", default=300)

# Writable fields per sensor type, beyond the disable periods.
SENSOR_FIELDS: dict[int, list[Field]] = {
    0x1: [],  # accelerometer: chip model is read-only
    0x2: [
        _LOG,
        Field("msItvl", "int", 1, 200, 1, "s", default=3),
        Field("tsThd", "float", 0, 20, 0.1, "°C", scale=10, default=0.5),
        Field("hsThd", "float", 0, 20, 0.1, "%", scale=10, default=3.0),
    ],
    0x10: [
        _LOG,
        Field("msItvl", "int", 1, 200, 1, "s", default=1),
        Field("bkOff", "int", 5, 3600, 1, "s", default=30),
    ],
    0x20: [
        _LOG,
        Field("msItvl", "int", 1, 200, 1, "s", default=5),
        Field("luxThd", "int", 0, 200, 1, "lx", default=20),
    ],
    0x40: [
        _LOG,
        Field("msItvl", "int", 3, 200, 1, "s", default=10),
        Field("vocThd", "int", 1, 250, 1, default=20),
        Field("noxThd", "int", 1, 250, 1, default=1),
    ],
    0x41: [
        _LOG,
        Field("msItvl", "int", 10, 3600, 1, "s", default=300),
        Field("co2Thd", "int", 0, 256, 1, "ppm", default=20),
        Field("asc", "bool", default=False),
    ],
    0x42: [
        Field("msItvl", "int", 1, 200, 1, "s", default=1),
        Field("pThd", "int", 256, 65535, 1, "mG", default=1000),
        Field("pDly", "int", 1, 100, 1, "x10 s", default=9),
    ],
    0x44: [
        Field("mode", "select", options={0: "legacy", 1: "long_range", 2: "extended"}, default=0),
        Field("rssi", "int", -100, 10, 1, "dBm", default=-80),
        Field("dur", "int", 1, 60000, 1, "x10 ms", default=200),
        Field("itvl", "int", 0, 7200, 1, "s", default=60),
        Field("mItvl", "int", 0, 7200, 1, "s", default=0),
        Field("aSlot", "int", 0, 4, 1, default=0),
        Field("max", "int", 1, 100, 1, default=20),
    ],
}
# Generic fields some firmwares report for any sensor.
GENERIC_FIELDS = [_LOG, Field("msItvl", "int", 1, 3600, 1, "s", default=10), _LOG_INTERVAL]

DISABLE_PERIOD_KEYS = ("dPrd0", "dPrd1", "dPrd2")


def sensor_fields(sensor: dict[str, Any]) -> list[Field]:
    """Fields to offer for a sensor: its known ones plus generic ones it reports."""
    known = SENSOR_FIELDS.get(int(sensor.get("srType", 0)))
    fields = list(known) if known is not None else []
    keys = {f.key for f in fields}
    fields += [f for f in GENERIC_FIELDS if f.key in sensor and f.key not in keys]
    return fields


def disable_periods(sensor: dict[str, Any]) -> list[str]:
    """Disable-period fields the firmware supports (reports) for this sensor.

    Firmwares that do not report them silently ignore writes (seen on a K4P
    accelerometer), so they are only offered when present.
    """
    return [key for key in DISABLE_PERIOD_KEYS if key in sensor]


def configurable(sensor: dict[str, Any]) -> bool:
    """True when the sensor has anything that can be written."""
    return bool(sensor_fields(sensor) or disable_periods(sensor))


def to_ui(field: Field, value: Any) -> Any:
    """Beacon value -> form value."""
    if value is None:
        return field.default
    if field.kind == "bool":
        return bool(value)
    if field.kind == "select":
        return str(int(value))
    return value / field.scale if field.scale != 1 else value


def from_ui(field: Field, value: Any) -> Any:
    """Form value -> beacon value."""
    if field.kind == "bool":
        return 1 if value else 0
    if field.kind == "select":
        return int(value)
    if field.scale != 1:
        return int(round(float(value) * field.scale))
    return int(value) if field.kind == "int" else float(value)


# -- disable periods (daily time ranges, stored in UTC) -------------------------


def encode_period(start: time, end: time, tz: tzinfo, today: datetime) -> int:
    """Local start/end -> the beacon's UTC integer (startH, startM, endH, endM)."""
    def utc(t: time) -> datetime:
        local = datetime.combine(today.date(), t, tzinfo=tz)
        return local.astimezone(tz=_UTC)

    s, e = utc(start), utc(end)
    return (s.hour << 24) | (s.minute << 16) | (e.hour << 8) | e.minute


def decode_period(value: int, tz: tzinfo, today: datetime) -> tuple[time, time] | None:
    """The beacon's UTC integer -> local (start, end); None when disabled."""
    if not value:
        return None
    sh, sm, eh, em = (value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF

    def local(h: int, m: int) -> time:
        utc = datetime.combine(today.date(), time(h % 24, m % 60), tzinfo=_UTC)
        return utc.astimezone(tz).time().replace(second=0, microsecond=0)

    return local(sh, sm), local(eh, em)


_UTC = timezone.utc


# -- trigger parameters -------------------------------------------------------

# trPara meaning per trigger type (motion and periodic are handled separately).
TRIGGER_PARAM: dict[int, Field] = {
    2: Field("trPara", "int", -90, 90, 1, "°", default=-45),  # angle: below this
    8: Field("trPara", "int", -50, 1000, 1, "°C", default=60),
    9: Field("trPara", "int", -50, 1000, 1, "°C", default=-10),
    10: Field("trPara", "int", 1, 99, 1, "%", default=80),
    11: Field("trPara", "int", 1, 99, 1, "%", default=20),
    12: Field("trPara", "int", 3, 36000, 1, "s", default=300),
    13: Field("trPara", "int", 5, 3600, 1, "s", default=30),
    14: Field("trPara", "int", 1, 65535, 1, "lx", default=1000),
    15: Field("trPara", "int", 1, 65535, 1, "lx", default=10),
    20: Field("trPara", "int", 100, 43200000, 100, "ms", default=60000),
}
# Extra fields of the angle trigger.
ANGLE_FIELDS = [
    Field("aAng", "int", -90, 90, 1, "°", default=45),
    Field("rptPrd", "int", 0, 255, 1, "min", default=0),
]

# -- KSensor advertisement content -------------------------------------------

# Content flag -> the sensor type that provides it (None: always available).
KSENSOR_CONTENT: dict[str, int | None] = {
    "axis": 0x1,
    "ht": 0x2,
    "pir": 0x10,
    "lux": 0x20,
    "voc": 0x40,
    "co2": 0x41,
    "mag": 0x42,
    "rcd": None,
}


def ksensor_content(config: dict[str, Any]) -> list[str]:
    """Content flags that make sense for this beacon's sensors."""
    present = {int(s.get("srType", 0)) for s in config.get("srObj", [])}
    return [key for key, sensor in KSENSOR_CONTENT.items() if sensor is None or sensor in present]
