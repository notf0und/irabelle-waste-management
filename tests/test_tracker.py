"""Bin status state machine."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from custom_components.irabelle_waste_management.tracker import BinTracker, TrackerSettings

TZ = ZoneInfo("Pacific/Auckland")
SETTINGS = TrackerSettings(time(5), time(19), timedelta(minutes=10), timedelta(seconds=120))


def at(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, second, tzinfo=TZ)


def feed_idle(tracker: BinTracker, start: datetime, minutes: float, step: int = 10) -> datetime:
    now = start
    while now <= start + timedelta(minutes=minutes):
        tracker.on_idle_frame(now, "proxy", -70)
        now += timedelta(seconds=step)
    return now


def home(start: datetime) -> BinTracker:
    tracker = BinTracker(SETTINGS, started_at=start)
    feed_idle(tracker, start, 3)
    assert tracker.status == "filling"
    return tracker


def test_filling_needs_constant_idle_frames() -> None:
    tracker = BinTracker(SETTINGS, started_at=at(14, 18))
    feed_idle(tracker, at(14, 18), 1)
    assert tracker.status is None
    feed_idle(tracker, at(14, 18, 1, 10), 1.5)
    assert tracker.status == "filling"


def test_out_motion_emptied_and_back() -> None:
    tracker = home(at(14, 17))
    tracker.on_idle_frame(at(14, 20), "p", -80)
    tracker.evaluate(at(14, 20, 9))
    assert tracker.status == "filling"
    tracker.evaluate(at(14, 20, 11))
    assert tracker.status == "awaiting_collection"
    tracker.on_motion_frame(at(15, 4, 30))  # before the window
    assert tracker.status == "awaiting_collection"
    tracker.on_motion_frame(at(15, 7, 10))
    assert (tracker.status, tracker.status_reason) == ("emptied", "motion")
    tracker.on_idle_frame(at(15, 9), "p", -95)  # stray frame from the kerb
    tracker.evaluate(at(15, 9, 5))
    assert tracker.status == "emptied"
    feed_idle(tracker, at(15, 17), 3)
    assert tracker.status == "filling"


def test_fallback_when_out_before_window() -> None:
    tracker = home(at(14, 18))
    tracker.evaluate(at(14, 20, 15))
    tracker.evaluate(at(15, 18, 59))
    assert tracker.status == "awaiting_collection"
    tracker.evaluate(at(15, 19))
    assert (tracker.status, tracker.status_reason) == ("emptied", "window_end_fallback")


def test_fallback_on_collection_day_when_put_out_late() -> None:
    tracker = home(at(15, 5))
    tracker.on_idle_frame(at(15, 6), "p", -70)
    tracker.evaluate(at(15, 6, 11), collection_today=True)
    tracker.evaluate(at(15, 18, 59), collection_today=True)
    assert tracker.status == "awaiting_collection"
    tracker.evaluate(at(15, 19), collection_today=True)
    assert tracker.status == "emptied"


def test_no_same_day_fallback_without_collection() -> None:
    tracker = home(at(15, 5))
    tracker.on_idle_frame(at(15, 6), "p", -70)
    tracker.evaluate(at(15, 6, 11))
    tracker.evaluate(at(15, 19, 1))
    assert tracker.status == "awaiting_collection"


def test_put_out_after_window_on_collection_day() -> None:
    tracker = home(at(15, 5))
    tracker.on_idle_frame(at(15, 19, 30), "p", -70)
    tracker.evaluate(at(15, 19, 41), collection_today=True)
    tracker.evaluate(at(15, 21), collection_today=True)
    assert tracker.status == "awaiting_collection"


def test_restart_grace_period() -> None:
    tracker = BinTracker(SETTINGS, started_at=at(15, 12))
    tracker.restore(
        {"status": "filling", "last_seen": at(15, 9).isoformat()}, datetime.fromisoformat
    )
    tracker.evaluate(at(15, 12, 5))
    assert tracker.status == "filling"
    tracker.evaluate(at(15, 12, 11))
    assert tracker.status == "awaiting_collection"


def test_manual_status_sticks_until_the_beacon_disagrees() -> None:
    tracker = home(at(15, 8))
    # Marked emptied while it is still heard at home: needs a fresh idle stretch
    tracker.set_manually(at(15, 9), "emptied")
    assert (tracker.status, tracker.status_reason) == ("emptied", "manual")
    tracker.on_idle_frame(at(15, 9, 0, 10), "p", -70)
    assert tracker.status == "emptied"
    feed_idle(tracker, at(15, 9, 0, 20), 3)
    assert tracker.status == "filling"
    # Marked filling while not heard: the away countdown restarts from the correction
    tracker.set_manually(at(15, 12), "awaiting_collection")
    tracker.set_manually(at(15, 13), "filling")
    tracker.evaluate(at(15, 13, 9))
    assert tracker.status == "filling"
    tracker.evaluate(at(15, 13, 11))
    assert tracker.status == "awaiting_collection"


def test_battery_clamped_and_persisted() -> None:
    tracker = BinTracker(SETTINGS, started_at=at(15, 5))
    tracker.battery_reported = 117
    assert tracker.battery() == 100
    tracker.battery_reported = 37
    restored = BinTracker(SETTINGS, started_at=at(15, 5))
    restored.restore(tracker.as_dict(), datetime.fromisoformat)
    assert restored.battery() == 37
