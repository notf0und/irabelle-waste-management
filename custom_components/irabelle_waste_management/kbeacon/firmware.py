"""KKM firmware catalogue (pure): what the KBeacon apps would offer.

KKM publishes one JSON file per model (see KBProBatchDFU, CfgBeaconDFUActivity):

    https://download.kkmiot.com:8093/KBeaconFirmware/<model>.json
    {"model": "K4p_NRF52XX",
     "V2.0": [{"appFileName": "...zip", "appVersion": "V6.56", "note": "..."}]}

Firmware is listed per *hardware version* (the beacon's ``hver``); the KKM
tool only offers an entry whose ``appVersion`` is newer than the beacon's,
compares versions as numbers (``V6.73`` -> 6.73) and installs the last entry,
showing the notes of every newer one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

CATALOG_URL = "https://download.kkmiot.com:8093/KBeaconFirmware/{model}.json"
DOWNLOAD_URL = "https://download.kkmiot.com:8093/KBeaconFirmware/{file}"


@dataclass(frozen=True, slots=True)
class FirmwareStatus:
    """Firmware situation of one beacon."""

    installed: str | None
    latest: str | None  # newest published for its hardware (None: none published)
    update_file: str | None  # package to install when newer than installed
    notes: str
    hardware_listed: bool


def _number(version: str | None) -> float | None:
    if not version:
        return None
    try:
        return float(version.lstrip("Vv"))
    except ValueError:
        return None


def firmware_status(catalog: dict[str, Any] | None, config: dict[str, Any]) -> FirmwareStatus:
    """Compare a beacon's firmware with the catalogue for its hardware."""
    installed = config.get("ver")
    hardware = config.get("hver")
    entries = (catalog or {}).get(hardware) if hardware else None
    if not isinstance(entries, list) or not entries:
        return FirmwareStatus(installed, None, None, "", hardware_listed=False)

    current = _number(installed)
    valid = [
        e for e in entries
        if isinstance(e, dict) and _number(e.get("appVersion")) is not None
    ]
    if not valid:
        return FirmwareStatus(installed, None, None, "", hardware_listed=False)
    last = valid[-1]
    newer = [
        e for e in valid
        if current is None or _number(e["appVersion"]) > current  # type: ignore[operator]
    ]
    notes = "\n".join(str(e["note"]).strip() for e in newer if e.get("note"))
    if current is not None and _number(last["appVersion"]) <= current:  # type: ignore[operator]
        return FirmwareStatus(installed, last["appVersion"], None, "", hardware_listed=True)
    return FirmwareStatus(
        installed, last["appVersion"], last.get("appFileName"), notes, hardware_listed=True
    )
