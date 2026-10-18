"""KKM firmware catalogue matching (same rules as KKM's DFU tool)."""

from __future__ import annotations

from custom_components.irabelle_waste_management.kbeacon.firmware import firmware_status

# KKM's published catalogue for the K4P (October 2026): hardware V2.0 only.
PUBLISHED = {
    "model": "K4p_NRF52XX",
    "V2.0": [{"appFileName": "K4p_dfu_52833_V6.56.zip", "appVersion": "V6.56",
              "note": "-bugfix for ST 3dh accleration."}],
}


def test_hardware_not_published(k4p) -> None:
    status = firmware_status(PUBLISHED, k4p)  # the K4P here is hardware V3.2
    assert (status.installed, status.latest, status.update_file) == ("V6.73", None, None)
    assert not status.hardware_listed


def test_published_but_older(k4p) -> None:
    catalog = {"V3.2": [{"appFileName": "a.zip", "appVersion": "V6.70"}]}
    status = firmware_status(catalog, k4p)
    assert status.hardware_listed and status.latest == "V6.70" and status.update_file is None


def test_update_available_with_notes(k4p) -> None:
    catalog = {"V3.2": [
        {"appFileName": "a.zip", "appVersion": "V6.70", "note": "old"},
        {"appFileName": "b.zip", "appVersion": "V6.80", "note": "-fix A"},
        {"appFileName": "c.zip", "appVersion": "V6.91", "note": "-fix B"},
    ]}
    status = firmware_status(catalog, k4p)
    assert (status.latest, status.update_file) == ("V6.91", "c.zip")
    assert status.notes == "-fix A\n-fix B"


def test_no_catalogue(k4p) -> None:
    assert not firmware_status(None, k4p).hardware_listed
