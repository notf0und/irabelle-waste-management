"""Repair notices."""

from __future__ import annotations

from custom_components.irabelle_waste_management.issues import parse_esphome_version


def test_parse_esphome_versions() -> None:
    assert parse_esphome_version("2026.9.1 (2026-10-17 16:25:31 +1300)") == (2026, 9, 1)
    assert parse_esphome_version("2.1 (ESPHome 2026.5.3)") == (2026, 5, 3)
    assert parse_esphome_version("V6.73") is None
    assert parse_esphome_version(None) is None
    assert (2026, 5, 3) < (2026, 8, 0) <= (2026, 9, 1)
