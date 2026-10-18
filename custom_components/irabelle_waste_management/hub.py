"""Runtime for one Irabelle Waste Management hub (config entry)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import logging
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
    async_track_time_interval,
)
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    BATTERY_RETRY_MINUTES,
    BATTERY_SWEEP_HOURS,
    BATTERY_SWEEP_SECONDS,
    CALENDAR_LOOKAHEAD_DAYS,
    CALENDAR_REFRESH_MINUTES,
    CONF_ADDRESS,
    CONF_AWAY_MINUTES,
    CONF_BATTERY_CAPACITY,
    CONF_BATTERY_EFFICIENCY,
    CONF_BEACON_PASSWORD,
    CONF_CALENDAR,
    CONF_IDLE_UUID,
    CONF_KEYWORD,
    CONF_MOTION_UUID,
    CONF_RETURN_SECONDS,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_AWAY_MINUTES,
    DEFAULT_RETURN_SECONDS,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOMAIN,
    MOTION_HOLD_SECONDS,
    MOTION_UUID_AUTO,
    SAVE_DELAY_SECONDS,
    SIGNAL_BIN_UPDATED,
    SIGNAL_HUB_UPDATED,
    SIGNAL_UPDATE_SECONDS,
    STATUS_AWAITING,
    STATUS_EMPTIED,
    STATUS_FILLING,
    STORAGE_VERSION,
    SUBENTRY_BIN,
    TICK_SECONDS,
)
from .kbeacon.power import DEFAULT_CAPACITY_MAH, DEFAULT_EFFICIENCY
from .kbeacon.protocol import DEFAULT_PASSWORD
from .ibeacon import (
    frame_from_service_info,
    kkm_battery_from_service_info,
    normalize_uuid,
)
from . import issues
from .beacon import BeaconManager
from .tracker import BinTracker, TrackerSettings

_LOGGER = logging.getLogger(__name__)

# Seconds after start-up before reading beacons that were never read.
FIRST_READ_DELAY = 120
# Seconds after start-up before the first repair-notice check.
ISSUE_CHECK_DELAY = 300

DUE_TODAY = "today"
DUE_TOMORROW = "tomorrow"


@dataclass(slots=True)
class Bin:
    """One configured bin and its live state."""

    subentry_id: str
    name: str
    address: str
    idle_uuid: str
    motion_uuid: str
    calendar: str | None
    keyword: str
    tracker: BinTracker
    next_collection: date | None = None
    signal: int | None = None
    signal_updated: datetime | None = None
    in_range: bool = False
    moving: bool = False
    password: str = DEFAULT_PASSWORD
    capacity_mah: float = DEFAULT_CAPACITY_MAH
    efficiency: float = DEFAULT_EFFICIENCY

    def frame_kind(self, uuid: str) -> str | None:
        """Classify a frame from this bin's MAC as 'idle', 'motion' or None."""
        if uuid == self.idle_uuid:
            return "idle"
        if self.motion_uuid in (MOTION_UUID_AUTO, uuid):
            return "motion"
        return None


def hub_option(entry: ConfigEntry, key: str) -> Any:
    """Read a hub setting from the entry options, falling back to defaults."""
    defaults = {
        CONF_WINDOW_START: DEFAULT_WINDOW_START,
        CONF_WINDOW_END: DEFAULT_WINDOW_END,
        CONF_AWAY_MINUTES: DEFAULT_AWAY_MINUTES,
        CONF_RETURN_SECONDS: DEFAULT_RETURN_SECONDS,
    }
    return entry.options.get(key, defaults[key])


class WasteHub:
    """Tracks every bin of a config entry and the hub-level summaries."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the hub from the entry and its bin subentries."""
        self.hass = hass
        self.entry = entry
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}"
        )
        self._unsubs: list[CALLBACK_TYPE] = []
        self._motion_timers: dict[str, CALLBACK_TYPE] = {}
        self._calendar_errors: set[str] = set()
        self._calendar_refresh_pending = False
        self._battery_retry: CALLBACK_TYPE | None = None
        self.beacons = BeaconManager(hass, self)

        settings = TrackerSettings(
            window_start=dt_util.parse_time(hub_option(entry, CONF_WINDOW_START)),
            window_end=dt_util.parse_time(hub_option(entry, CONF_WINDOW_END)),
            away=timedelta(minutes=float(hub_option(entry, CONF_AWAY_MINUTES))),
            confirm=timedelta(seconds=float(hub_option(entry, CONF_RETURN_SECONDS))),
        )
        self.settings = settings
        started = dt_util.now()
        self.bins: dict[str, Bin] = {}
        for subentry in entry.subentries.values():
            if subentry.subentry_type != SUBENTRY_BIN:
                continue
            data = subentry.data
            self.bins[subentry.subentry_id] = Bin(
                subentry_id=subentry.subentry_id,
                name=subentry.title,
                address=data[CONF_ADDRESS].upper(),
                idle_uuid=normalize_uuid(data[CONF_IDLE_UUID]),
                motion_uuid=(
                    MOTION_UUID_AUTO
                    if data.get(CONF_MOTION_UUID, MOTION_UUID_AUTO) == MOTION_UUID_AUTO
                    else normalize_uuid(data[CONF_MOTION_UUID])
                ),
                calendar=data.get(CONF_CALENDAR) or None,
                keyword=(data.get(CONF_KEYWORD) or "").strip().casefold(),
                tracker=BinTracker(settings=settings, started_at=started),
                password=data.get(CONF_BEACON_PASSWORD) or DEFAULT_PASSWORD,
                capacity_mah=float(data.get(CONF_BATTERY_CAPACITY) or DEFAULT_CAPACITY_MAH),
                efficiency=float(data.get(CONF_BATTERY_EFFICIENCY) or DEFAULT_EFFICIENCY),
            )

    # -- lifecycle --------------------------------------------------------

    async def async_setup(self) -> None:
        """Restore state and start listening."""
        await self.beacons.async_load()
        stored = await self._store.async_load() or {}
        for bin_ in self.bins.values():
            if saved := stored.get(bin_.subentry_id):
                bin_.tracker.restore(saved, _parse_local)

        for bin_ in self.bins.values():
            self._unsubs.append(
                bluetooth.async_register_advertisement_callback(
                    self.hass, self._make_advertisement_handler(bin_), bin_.address
                )
            )
        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_tick, timedelta(seconds=TICK_SECONDS)
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_refresh_calendars,
                timedelta(minutes=CALENDAR_REFRESH_MINUTES),
            )
        )
        # Re-read just after midnight so "today"/"tomorrow" roll over promptly.
        self._unsubs.append(
            async_track_time_change(
                self.hass, self._async_refresh_calendars, hour=0, minute=0, second=30
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._async_battery_sweep,
                timedelta(hours=BATTERY_SWEEP_HOURS),
            )
        )
        self._unsubs.append(async_at_started(self.hass, self._async_battery_sweep))

        @callback
        def _first_read(_hass: HomeAssistant) -> None:
            @callback
            def _later(_now: datetime) -> None:
                self.entry.async_create_background_task(
                    self.hass, self.beacons.async_read_missing(), f"{DOMAIN} first read"
                )

            self._unsubs.append(async_call_later(self.hass, FIRST_READ_DELAY, _later))

        self._unsubs.append(async_at_started(self.hass, _first_read))

        @callback
        def _check_issues(_now: Any = None) -> None:
            issues.async_check(self)

        issues.async_remove_orphans(self)
        self._unsubs.append(
            async_track_time_interval(self.hass, _check_issues, timedelta(hours=1))
        )
        # Give the proxies a few minutes to hear everything after a restart.
        self._unsubs.append(async_call_later(self.hass, ISSUE_CHECK_DELAY, _check_issues))
        # Calendars may not exist yet, or not have loaded their data, while
        # Home Assistant starts: refresh again whenever one of them changes.
        if calendars := sorted({b.calendar for b in self.bins.values() if b.calendar}):
            self._unsubs.append(
                async_track_state_change_event(
                    self.hass, calendars, self._async_calendar_changed
                )
            )
        self._async_schedule_calendar_refresh()

    @callback
    def _async_calendar_changed(self, _event) -> None:
        self._async_schedule_calendar_refresh()

    @callback
    def _async_schedule_calendar_refresh(self) -> None:
        if self._calendar_refresh_pending:
            return
        self._calendar_refresh_pending = True

        async def _refresh() -> None:
            self._calendar_refresh_pending = False
            await self._async_refresh_calendars()

        self.entry.async_create_background_task(
            self.hass, _refresh(), f"{DOMAIN} calendars"
        )

    async def async_unload(self) -> None:
        """Stop listening and flush state to disk."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        if self._battery_retry is not None:
            self._battery_retry()
            self._battery_retry = None
        for cancel in self._motion_timers.values():
            cancel()
        self._motion_timers.clear()
        await self._store.async_save(self._data_to_save())

    async def _async_battery_sweep(self, _now: Any = None) -> None:
        """Ask the proxies for a short active scan to collect battery levels."""
        if not self.bins:
            return
        try:
            await bluetooth.async_request_active_scan(self.hass, BATTERY_SWEEP_SECONDS)
        except Exception as err:  # noqa: BLE001 - best effort only
            _LOGGER.debug("Active scan request failed: %s", err)
        if self._battery_retry is None and any(
            b.tracker.battery_reported is None for b in self.bins.values()
        ):

            @callback
            def _retry(_now: datetime) -> None:
                self._battery_retry = None
                self.entry.async_create_background_task(
                    self.hass, self._async_battery_sweep(), f"{DOMAIN} battery sweep"
                )

            self._battery_retry = async_call_later(
                self.hass, timedelta(minutes=BATTERY_RETRY_MINUTES), _retry
            )

    # -- advertisements ---------------------------------------------------

    def _make_advertisement_handler(self, bin_: Bin):
        @callback
        def _handle(service_info: bluetooth.BluetoothServiceInfoBleak) -> None:
            battery = kkm_battery_from_service_info(service_info)
            if battery is not None and battery != bin_.tracker.battery_reported:
                bin_.tracker.battery_reported = battery
                self._bin_changed(bin_)
            if (frame := frame_from_service_info(service_info)) is None:
                return
            kind = bin_.frame_kind(frame.uuid)
            if kind is None:
                return
            now = dt_util.now()
            _LOGGER.debug(
                "%s: %s frame via %s rssi=%s raw=%s",
                bin_.name, kind, service_info.source, service_info.rssi,
                service_info.raw is not None,
            )
            if kind == "idle":
                changed = bin_.tracker.on_idle_frame(
                    now, service_info.source, service_info.rssi
                )
                if not bin_.in_range:
                    bin_.in_range = True
                    changed = True
            else:
                changed = bin_.tracker.on_motion_frame(now) or not bin_.moving
                bin_.moving = True
                self._schedule_motion_off(bin_)
            if changed:
                self._bin_changed(bin_)

        return _handle

    @callback
    def _schedule_motion_off(self, bin_: Bin) -> None:
        if cancel := self._motion_timers.pop(bin_.subentry_id, None):
            cancel()

        @callback
        def _off(_now: datetime) -> None:
            self._motion_timers.pop(bin_.subentry_id, None)
            if bin_.moving and not bin_.tracker.moving(dt_util.now()):
                bin_.moving = False
                self._bin_changed(bin_)

        self._motion_timers[bin_.subentry_id] = async_call_later(
            self.hass, MOTION_HOLD_SECONDS + 0.5, _off
        )

    # -- periodic work ----------------------------------------------------

    @callback
    def _async_tick(self, _now: datetime | None = None) -> None:
        now = dt_util.now()
        for bin_ in self.bins.values():
            changed = bin_.tracker.evaluate(
                now, collection_today=bin_.next_collection == now.date()
            )
            in_range = bin_.tracker.in_range(now)
            if in_range != bin_.in_range:
                bin_.in_range = in_range
                changed = True
            if (
                bin_.signal_updated is None
                or now - bin_.signal_updated >= timedelta(seconds=SIGNAL_UPDATE_SECONDS)
            ):
                signal = bin_.tracker.best_rssi(now)
                if signal != bin_.signal:
                    bin_.signal = signal
                    bin_.signal_updated = now
                    changed = True
            if changed:
                self._bin_changed(bin_)

    async def _async_refresh_calendars(self, _now: datetime | None = None) -> None:
        by_calendar: dict[str, list[Bin]] = {}
        for bin_ in self.bins.values():
            if bin_.calendar:
                by_calendar.setdefault(bin_.calendar, []).append(bin_)
        if not by_calendar:
            return
        if not self.hass.services.has_service("calendar", "get_events"):
            # Still starting up; the calendar state listener retries later.
            return

        start = dt_util.start_of_local_day()
        today = start.date()
        for calendar, bins in by_calendar.items():
            try:
                response = await self.hass.services.async_call(
                    "calendar",
                    "get_events",
                    {
                        "entity_id": calendar,
                        "start_date_time": start.isoformat(),
                        "duration": {"days": CALENDAR_LOOKAHEAD_DAYS},
                    },
                    blocking=True,
                    return_response=True,
                )
            except (HomeAssistantError, ValueError) as err:
                if calendar not in self._calendar_errors:
                    _LOGGER.warning("Could not read %s: %s", calendar, err)
                    self._calendar_errors.add(calendar)
                continue
            self._calendar_errors.discard(calendar)
            events = (response or {}).get(calendar, {}).get("events", [])
            for bin_ in bins:
                bin_.next_collection = _next_collection(events, bin_.keyword, today)
                _LOGGER.debug(
                    "%s: %d event(s) in %s, next '%s' collection %s",
                    bin_.name, len(events), calendar, bin_.keyword, bin_.next_collection,
                )
        self.async_notify_hub()
        for bin_ in self.bins.values():
            async_dispatcher_send(
                self.hass, SIGNAL_BIN_UPDATED.format(bin_.subentry_id)
            )

    # -- notifications and persistence -----------------------------------

    @callback
    def async_set_status(self, bin_: Bin, status: str) -> None:
        """Correct a bin's status by hand."""
        bin_.tracker.set_manually(dt_util.now(), status)
        self._bin_changed(bin_)

    @callback
    def async_bin_changed(self, bin_: Bin) -> None:
        """Push a bin's new state to its entities and save it."""
        self._bin_changed(bin_)

    @callback
    def _bin_changed(self, bin_: Bin) -> None:
        async_dispatcher_send(self.hass, SIGNAL_BIN_UPDATED.format(bin_.subentry_id))
        self.async_notify_hub()
        self._store.async_delay_save(self._data_to_save, SAVE_DELAY_SECONDS)

    @callback
    def async_notify_hub(self) -> None:
        """Tell the hub-level entities to refresh."""
        async_dispatcher_send(self.hass, SIGNAL_HUB_UPDATED.format(self.entry.entry_id))

    def _data_to_save(self) -> dict[str, Any]:
        return {sid: bin_.tracker.as_dict() for sid, bin_ in self.bins.items()}

    # -- queries used by the entities ------------------------------------

    def due(self, bin_: Bin, now: datetime | None = None) -> str | None:
        """Return 'today', 'tomorrow' or None for the bin's next collection."""
        if bin_.next_collection is None:
            return None
        now = now or dt_util.now()
        today = now.date()
        if bin_.next_collection == today:
            current = now.timetz().replace(tzinfo=None)
            return DUE_TODAY if current < self.settings.window_end else None
        if bin_.next_collection == today + timedelta(days=1):
            return DUE_TOMORROW
        return None

    def bins_to_take_out(self) -> list[Bin]:
        """Bins due today/tomorrow that are still at home."""
        return [
            b
            for b in self.bins.values()
            if b.tracker.status == STATUS_FILLING and self.due(b) is not None
        ]

    def bins_with_status(self, status: str) -> list[Bin]:
        """Bins currently in the given status."""
        return [b for b in self.bins.values() if b.tracker.status == status]

    def ready_to_bring_back(self) -> bool:
        """True once every bin that went out has been emptied."""
        return bool(self.bins_with_status(STATUS_EMPTIED)) and not (
            self.bins_with_status(STATUS_AWAITING)
        )


def _parse_local(value: str) -> datetime | None:
    if (parsed := dt_util.parse_datetime(value)) is None:
        return None
    return dt_util.as_local(parsed)


def _event_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return dt_util.as_local(value).date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if (parsed := dt_util.parse_datetime(value)) is not None:
            return dt_util.as_local(parsed).date()
        return dt_util.parse_date(value)
    return None


def _next_collection(
    events: list[dict[str, Any]], keyword: str, today: date
) -> date | None:
    dates = [
        event_date
        for event in events
        if keyword in str(event.get("summary", "")).casefold()
        and (event_date := _event_date(event.get("start"))) is not None
        and event_date >= today
    ]
    return min(dates, default=None)
