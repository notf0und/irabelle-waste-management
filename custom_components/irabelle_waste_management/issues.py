"""Repair notices for problems a household would not notice by itself."""

from __future__ import annotations

from datetime import timedelta
import re
from typing import TYPE_CHECKING

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, issue_registry as ir
from homeassistant.util import dt as dt_util

from .const import DOMAIN

if TYPE_CHECKING:
    from .hub import WasteHub

SILENT_AFTER = timedelta(days=3)
LOW_BATTERY_PERCENT = 20
LOW_BATTERY_DAYS = 90
# ESPHome proxies older than this cannot complete Bluetooth connections.
MIN_PROXY_ESPHOME = (2026, 8, 0)

_ESPHOME_IN_TEXT = re.compile(r"ESPHome (\d+)\.(\d+)\.(\d+)")
_LEADING_VERSION = re.compile(r"^(\d{4})\.(\d+)\.(\d+)")


def parse_esphome_version(sw_version: str | None) -> tuple[int, int, int] | None:
    """'2026.9.1 (...)' or '2.1 (ESPHome 2026.5.3)' -> (2026, 9, 1)."""
    if not sw_version:
        return None
    match = _ESPHOME_IN_TEXT.search(sw_version) or _LEADING_VERSION.match(sw_version)
    return tuple(int(part) for part in match.groups()) if match else None  # type: ignore[return-value]


@callback
def outdated_proxies(hass: HomeAssistant) -> list[str]:
    """Names of connectable ESPHome proxies whose firmware cannot connect."""
    registry = dr.async_get(hass)
    names: list[str] = []
    for scanner in bluetooth.async_current_scanners(hass):
        if not scanner.connectable:
            continue
        adapter = registry.async_get_device(
            connections={(dr.CONNECTION_BLUETOOTH, scanner.source)}
        )
        device = (
            registry.async_get(adapter.via_device_id)
            if adapter and adapter.via_device_id
            else adapter
        )
        if device is None:
            continue
        version = parse_esphome_version(device.sw_version)
        if version is not None and version < MIN_PROXY_ESPHOME:
            names.append(device.name_by_user or device.name or scanner.name)
    return sorted(set(names))


def _issue_id(kind: str, subentry_id: str) -> str:
    return f"{kind}_{subentry_id}"


@callback
def async_check(hub: WasteHub) -> None:
    """Create or clear the repair notices of a hub."""
    hass = hub.hass
    now = dt_util.now()

    for bin_ in hub.bins.values():
        tracker = bin_.tracker
        heard = max(
            (t for t in (tracker.last_seen, tracker.last_motion) if t is not None),
            default=None,
        )
        silent_since = heard or tracker.started_at
        issue = _issue_id("beacon_silent", bin_.subentry_id)
        if now - silent_since >= SILENT_AFTER:
            ir.async_create_issue(
                hass, DOMAIN, issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="beacon_silent",
                translation_placeholders={
                    "bin": bin_.name,
                    "days": str((now - silent_since).days),
                },
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue)

        level = tracker.battery()
        estimate = hub.beacons.power_estimate(bin_)
        days_left = (
            round(estimate["life_days"] * (level if level is not None else 100) / 100)
            if estimate
            else None
        )
        issue = _issue_id("battery_low", bin_.subentry_id)
        if (level is not None and level <= LOW_BATTERY_PERCENT) or (
            days_left is not None and days_left < LOW_BATTERY_DAYS
        ):
            ir.async_create_issue(
                hass, DOMAIN, issue,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="battery_low",
                translation_placeholders={
                    "bin": bin_.name,
                    "battery": str(level if level is not None else "?"),
                    "days": str(days_left if days_left is not None else "?"),
                },
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue)

    async_check_proxies(hass)


@callback
def async_check_proxies(hass: HomeAssistant) -> None:
    """Warn about Bluetooth proxies whose firmware cannot configure beacons."""
    if outdated := outdated_proxies(hass):
        ir.async_create_issue(
            hass, DOMAIN, "proxy_firmware",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="proxy_firmware",
            translation_placeholders={
                "proxies": ", ".join(outdated),
                "minimum": ".".join(str(p) for p in MIN_PROXY_ESPHOME),
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, "proxy_firmware")


@callback
def async_remove_orphans(hub: WasteHub) -> None:
    """Drop notices of bins that no longer exist."""
    registry = ir.async_get(hub.hass)
    known = set(hub.bins)
    for domain, issue_id in list(registry.issues):
        if domain != DOMAIN or issue_id == "proxy_firmware":
            continue
        subentry_id = issue_id.rsplit("_", 1)[-1]
        if subentry_id not in known:
            ir.async_delete_issue(hub.hass, DOMAIN, issue_id)
