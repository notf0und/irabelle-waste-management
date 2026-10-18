"""Sensor settings and disable periods."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from custom_components.irabelle_waste_management.kbeacon import sensors

TZ = ZoneInfo("Pacific/Auckland")
TODAY = datetime(2026, 10, 18, 12, tzinfo=TZ)  # NZDT, UTC+13


def test_disable_period_round_trip() -> None:
    value = sensors.encode_period(time(22, 0), time(6, 0), TZ, TODAY)
    assert value == (9 << 24) | (17 << 8)  # stored in UTC
    assert sensors.decode_period(value, TZ, TODAY) == (time(22, 0), time(6, 0))
    assert sensors.decode_period(0, TZ, TODAY) is None


def test_fields_and_scaling() -> None:
    fields = {f.key: f for f in sensors.sensor_fields({"srType": 2, "tsThd": 5})}
    assert sensors.to_ui(fields["tsThd"], 5) == 0.5
    assert sensors.from_ui(fields["tsThd"], 0.7) == 7
    assert sensors.from_ui(fields["log"], False) == 0


def test_k4p_accelerometer_is_not_configurable(k4p) -> None:
    accelerometer = k4p["srObj"][0]
    assert not sensors.configurable(accelerometer)
    assert sensors.configurable({**accelerometer, "dPrd0": 0})
    assert sensors.ksensor_content(k4p) == ["axis", "rcd"]
