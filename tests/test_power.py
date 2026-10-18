"""Power profiler, calibrated on KBeacon Pro's own figures for a K4P."""

from __future__ import annotations

from custom_components.irabelle_waste_management.kbeacon.power import estimate


def test_matches_kbeacon_pro(k4p) -> None:
    result = estimate(k4p)
    assert result["slots"][0]["current_ua"] == 11.0  # KBeacon Pro: 0.01100 mA
    assert result["triggered_advertising"][0]["current_ua"] == 25.52  # 0.02550 mA
    assert result["accelerometer_standby_ua"] == 0.1
    assert result["motion_sampling_ua"] == 3.0
    assert result["average_current_ua"] == 17.1  # 0.0171 mA
    assert result["life_months"] == 60.9  # 60.9 months


def test_capacity_and_efficiency_scale(k4p) -> None:
    assert estimate(k4p, 220, 75)["life_months"] == round(60.9 * 220 / 1000, 1)
    assert estimate(k4p, 1000, 100)["life_months"] > estimate(k4p)["life_months"]
