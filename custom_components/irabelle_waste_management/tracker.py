"""Bin status state machine.

Kept free of Home Assistant imports so it can be tested on its own. All
datetimes are timezone-aware and in the Home Assistant time zone.

    filling ──(no idle frame for `away`)──────────────► awaiting_collection
    awaiting_collection ──(motion inside the window)──► emptied
    awaiting_collection ──(window end; out since window start,
                            or out before window end on a collection day)──► emptied
    awaiting_collection / emptied ──(idle frames for `confirm`)──► filling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

from .const import (
    IDLE_STREAK_GAP_SECONDS,
    IN_RANGE_SECONDS,
    MOTION_HOLD_SECONDS,
    SIGNAL_WINDOW_SECONDS,
    STATUS_AWAITING,
    STATUS_EMPTIED,
    STATUS_FILLING,
)

REASON_MANUAL = "manual"

_STREAK_GAP = timedelta(seconds=IDLE_STREAK_GAP_SECONDS)
_IN_RANGE = timedelta(seconds=IN_RANGE_SECONDS)
_MOTION_HOLD = timedelta(seconds=MOTION_HOLD_SECONDS)
_SIGNAL_WINDOW = timedelta(seconds=SIGNAL_WINDOW_SECONDS)


@dataclass(slots=True)
class TrackerSettings:
    """Timing rules shared by every bin of a hub."""

    window_start: time
    window_end: time
    away: timedelta
    confirm: timedelta


@dataclass(slots=True)
class BinTracker:
    """Status of one bin, driven by its idle and motion advertisements."""

    settings: TrackerSettings
    started_at: datetime
    status: str | None = None
    status_since: datetime | None = None
    status_reason: str | None = None
    last_seen: datetime | None = None
    last_motion: datetime | None = None
    idle_streak_start: datetime | None = None
    # Raw battery byte from the KKM extension data (can exceed 100 when fresh).
    battery_reported: int | None = None
    _rssi_by_source: dict[str, tuple[int, datetime]] = field(default_factory=dict)

    # -- inputs -----------------------------------------------------------

    def on_idle_frame(self, now: datetime, source: str, rssi: int | None) -> bool:
        """Record an idle advertisement. Return True if the status changed."""
        if (
            self.idle_streak_start is None
            or self.last_seen is None
            or now - self.last_seen > _STREAK_GAP
        ):
            self.idle_streak_start = now
        self.last_seen = now
        if rssi is not None:
            self._rssi_by_source[source] = (rssi, now)
        return self.evaluate(now)

    def on_motion_frame(self, now: datetime) -> bool:
        """Record a motion advertisement. Return True if the status changed."""
        self.last_motion = now
        if self.status == STATUS_AWAITING and self._in_window(now):
            return self._set(STATUS_EMPTIED, now, "motion")
        return False

    def evaluate(self, now: datetime, collection_today: bool = False) -> bool:
        """Apply the time-based rules. Return True if the status changed.

        `collection_today` comes from the bin's calendar: on a collection day a
        bin put out after the window opened (e.g. at 06:00) still falls back to
        emptied at the window end, in case the truck's lift was not heard.
        """
        if self.status != STATUS_FILLING and self._idle_confirmed(now):
            return self._set(STATUS_FILLING, now, "idle_advertising")

        if self.status in (None, STATUS_FILLING):
            reference = self.last_seen or self.started_at
            # After a restart, give the bin a full `away` period to be heard.
            reference = max(reference, self.started_at)
            if self.status_reason == REASON_MANUAL and self.status_since:
                # A manual correction also restarts the countdown.
                reference = max(reference, self.status_since)
            if now - reference > self.settings.away:
                return self._set(STATUS_AWAITING, now, "no_idle_advertising")

        if (
            self.status == STATUS_AWAITING
            and now.timetz().replace(tzinfo=None) >= self.settings.window_end
            and self.status_since is not None
            and (
                self.status_since < self._today_at(now, self.settings.window_start)
                or (
                    collection_today
                    and self.status_since
                    < self._today_at(now, self.settings.window_end)
                )
            )
        ):
            return self._set(STATUS_EMPTIED, now, "window_end_fallback")

        return False

    def set_manually(self, now: datetime, status: str) -> bool:
        """Correct the status by hand; detection carries on from there."""
        if status not in (STATUS_FILLING, STATUS_AWAITING, STATUS_EMPTIED):
            raise ValueError(status)
        # Returning to filling needs a fresh stretch of idle frames, so the
        # correction is not undone by the streak that was already running.
        self.idle_streak_start = None
        changed = status != self.status
        self.status = status
        self.status_since = now
        self.status_reason = REASON_MANUAL
        return changed

    # -- derived state ----------------------------------------------------

    def in_range(self, now: datetime) -> bool:
        """Return True while idle frames are being heard."""
        return self.last_seen is not None and now - self.last_seen <= _IN_RANGE

    def moving(self, now: datetime) -> bool:
        """Return True shortly after a motion frame."""
        return self.last_motion is not None and now - self.last_motion < _MOTION_HOLD

    def battery(self) -> int | None:
        """Battery percent, clamped to 100 like the KKM app does."""
        if self.battery_reported is None:
            return None
        return min(self.battery_reported, 100)

    def best_rssi(self, now: datetime) -> int | None:
        """Return the strongest recent idle-frame RSSI across all proxies."""
        recent = [
            rssi
            for rssi, seen in self._rssi_by_source.values()
            if now - seen <= _SIGNAL_WINDOW
        ]
        return max(recent) if recent else None

    # -- persistence ------------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """Return the state worth keeping across restarts."""
        return {
            "status": self.status,
            "status_since": _iso(self.status_since),
            "status_reason": self.status_reason,
            "last_seen": _iso(self.last_seen),
            "last_motion": _iso(self.last_motion),
            "battery_reported": self.battery_reported,
        }

    def restore(self, data: dict[str, Any], parse) -> None:
        """Restore state saved by as_dict(); `parse` turns ISO text into datetimes."""
        self.status = data.get("status")
        self.status_reason = data.get("status_reason")
        self.battery_reported = data.get("battery_reported")
        for key in ("status_since", "last_seen", "last_motion"):
            if value := data.get(key):
                setattr(self, key, parse(value))

    # -- helpers ----------------------------------------------------------

    def _idle_confirmed(self, now: datetime) -> bool:
        return (
            self.idle_streak_start is not None
            and self.last_seen is not None
            and now - self.last_seen <= _STREAK_GAP
            and now - self.idle_streak_start >= self.settings.confirm
        )

    def _in_window(self, now: datetime) -> bool:
        current = now.timetz().replace(tzinfo=None)
        return self.settings.window_start <= current < self.settings.window_end

    @staticmethod
    def _today_at(now: datetime, at: time) -> datetime:
        return now.replace(
            hour=at.hour, minute=at.minute, second=at.second, microsecond=0
        )

    def _set(self, status: str, now: datetime, reason: str) -> bool:
        if status == self.status:
            return False
        self.status = status
        self.status_since = now
        self.status_reason = reason
        return True


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
