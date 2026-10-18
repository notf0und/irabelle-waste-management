"""KBeacon configuration from Home Assistant (connect, read, write, commands)."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Awaitable, Callable
import logging
from typing import TYPE_CHECKING, Any, TypeVar

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STORAGE_VERSION
from .kbeacon.client import KBeaconClient
from .kbeacon.power import estimate
from .kbeacon.protocol import KBeaconAuthError, KBeaconError, validate_password
from .kbeacon.settings import (
    SettingsImportError,
    build_import,
    export_settings,
)

if TYPE_CHECKING:
    from .hub import Bin, WasteHub

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")

CONNECT_ATTEMPTS = 3
BEEP_CAPABILITY = 0x2  # KBCfgCommon.isSupportBeep


class BeaconError(HomeAssistantError):
    """A beacon operation failed (shown to the user as is)."""


class BeaconImportError(BeaconError):
    """A settings template cannot be applied to this beacon."""


class BeaconLoginError(BeaconError):
    """The beacon rejected the password."""


def _fingerprint(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()[:16]


class BeaconManager:
    """Talks to the bins' KBeacons over Bluetooth, one session at a time each."""

    def __init__(self, hass: HomeAssistant, hub: WasteHub) -> None:
        """Initialise the manager."""
        self.hass = hass
        self.hub = hub
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{hub.entry.entry_id}.beacons"
        )
        self._locks: dict[str, asyncio.Lock] = {}
        # subentry_id -> {"config": {...}, "read_at": iso}
        self.cache: dict[str, dict[str, Any]] = {}

    async def async_load(self) -> None:
        """Load the cached beacon settings."""
        self.cache = await self._store.async_load() or {}
        for sid in list(self.cache):
            if sid not in self.hub.bins:
                self.cache.pop(sid)

    async def async_read_missing(self) -> None:
        """Read once from beacons never read before (model, firmware, slots)."""
        for bin_ in list(self.hub.bins.values()):
            entry = self.cache.get(bin_.subentry_id, {})
            if entry.get("config"):
                continue
            if entry.get("login_failed_with") == _fingerprint(bin_.password):
                # Never retry a password the beacon already rejected.
                continue
            try:
                await self.async_read(bin_)
            except BeaconLoginError as err:
                self.cache[bin_.subentry_id] = {
                    "login_failed_with": _fingerprint(bin_.password)
                }
                await self._store.async_save(self.cache)
                _LOGGER.warning("%s", err)
            except BeaconError as err:
                _LOGGER.info("First read of %s skipped: %s", bin_.name, err)

    def power_estimate(self, bin_: Bin) -> dict[str, Any] | None:
        """Battery-life estimate from the last settings read, if any."""
        if not (config := self.config(bin_)):
            return None
        return estimate(config, bin_.capacity_mah)

    def supports_identify(self, bin_: Bin) -> bool:
        """True when the beacon reports a beeper (KBeacon Pro offers ring then)."""
        config = self.config(bin_) or {}
        return bool(int(config.get("bCap", 0)) & BEEP_CAPABILITY)

    def config(self, bin_: Bin) -> dict[str, Any] | None:
        """Last settings read from the bin's beacon, if any."""
        return (self.cache.get(bin_.subentry_id) or {}).get("config")

    # -- sessions -----------------------------------------------------------

    async def _session(
        self, bin_: Bin, action: Callable[[KBeaconClient], Awaitable[_T]]
    ) -> _T:
        lock = self._locks.setdefault(bin_.subentry_id, asyncio.Lock())
        async with lock:
            device = bluetooth.async_ble_device_from_address(
                self.hass, bin_.address, connectable=True
            )
            if device is None:
                raise BeaconError(
                    f"{bin_.name}: the beacon is not in range of a Bluetooth proxy "
                    "that can connect"
                )
            try:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    f"{bin_.name} ({bin_.address})",
                    max_attempts=CONNECT_ATTEMPTS,
                )
            except (BleakError, TimeoutError) as err:
                raise BeaconError(f"{bin_.name}: could not connect: {err}") from err
            try:
                beacon = KBeaconClient(client, bin_.address)
                await beacon.login(bin_.password)
                return await action(beacon)
            except KBeaconAuthError as err:
                raise BeaconLoginError(
                    f"{bin_.name}: login failed ({err}). Check the beacon password "
                    "in the bin settings"
                ) from err
            except (KBeaconError, BleakError, TimeoutError) as err:
                raise BeaconError(f"{bin_.name}: {err or type(err).__name__}") from err
            finally:
                try:
                    await client.disconnect()
                except BleakError:
                    pass

    async def _remember(self, bin_: Bin, config: dict[str, Any]) -> dict[str, Any]:
        self.cache[bin_.subentry_id] = {
            "config": config,
            "read_at": dt_util.utcnow().isoformat(),
        }
        await self._store.async_save(self.cache)
        self._update_device(bin_, config)
        if (battery := config.get("btPt")) is not None:
            bin_.tracker.battery_reported = battery
        self.hub.async_bin_changed(bin_)
        return config

    def _update_device(self, bin_: Bin, config: dict[str, Any]) -> None:
        registry = dr.async_get(self.hass)
        if device := registry.async_get_device(identifiers={(DOMAIN, bin_.subentry_id)}):
            registry.async_update_device(
                device.id,
                model=config.get("model"),
                sw_version=config.get("ver"),
                hw_version=config.get("hver"),
            )

    # -- operations ----------------------------------------------------------

    async def async_read(self, bin_: Bin) -> dict[str, Any]:
        """Read every setting from the beacon (and cache it)."""
        config = await self._session(bin_, lambda kb: kb.read_config())
        return await self._remember(bin_, config)

    async def async_write(self, bin_: Bin, payload: dict[str, Any]) -> dict[str, Any]:
        """Write settings, then read everything back."""
        if "pwd" in payload:
            raise BeaconError("Use the change-password action to change the password")

        async def _write(kb: KBeaconClient) -> dict[str, Any]:
            await kb.write_config(payload)
            return await kb.read_config()

        return await self._remember(bin_, await self._session(bin_, _write))

    async def async_export(self, bin_: Bin, refresh: bool = True) -> dict[str, Any]:
        """Return a shareable settings template from the beacon."""
        config = await self.async_read(bin_) if refresh else self.config(bin_)
        if not config:
            raise BeaconError(f"{bin_.name}: no settings have been read yet")
        return export_settings(config)

    async def async_import(
        self, bin_: Bin, template: dict[str, Any], allow_other_model: bool = False
    ) -> dict[str, Any]:
        """Apply a template, keeping the beacon's name, password and UUIDs."""

        async def _import(kb: KBeaconClient) -> dict[str, Any]:
            current = await kb.read_config()
            try:
                payload = build_import(template, current, allow_other_model)
            except SettingsImportError as err:
                raise BeaconImportError(str(err)) from err
            await kb.write_config(payload)
            return await kb.read_config()

        return await self._remember(bin_, await self._session(bin_, _import))

    async def async_identify(self, bin_: Bin, seconds: int = 10) -> None:
        """Flash the beacon's LED."""
        ring_type = 0x3 if self.supports_identify(bin_) else 0x2  # beep + LED
        await self._session(bin_, lambda kb: kb.ring(seconds * 1000, ring_type))

    async def async_reset(self, bin_: Bin) -> None:
        """Factory-reset the beacon's configuration."""
        await self._session(bin_, lambda kb: kb.reset())
        self.cache.pop(bin_.subentry_id, None)
        await self._store.async_save(self.cache)

    async def async_power_off(self, bin_: Bin) -> None:
        """Power the beacon off."""
        await self._session(bin_, lambda kb: kb.power_off())

    async def async_change_password(self, bin_: Bin, new_password: str) -> None:
        """Change the beacon password, verifying it by logging in again.

        The caller stores the new password in the bin settings afterwards.
        """
        try:
            validate_password(new_password)
        except ValueError as err:
            raise BeaconError(str(err)) from err
        await self._session(bin_, lambda kb: kb.write_config({"pwd": new_password}))
        # The beacon accepted it: from now on only the new password works.
        bin_.password = new_password
        try:
            await self._session(bin_, lambda kb: kb.read_config(sensors=False))
        except BeaconError as err:
            raise BeaconError(
                f"{bin_.name}: the beacon accepted the new password and it was saved, "
                f"but logging in with it to verify failed ({err}). If it keeps "
                "failing, reset the beacon with its button"
            ) from err
