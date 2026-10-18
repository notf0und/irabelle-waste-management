"""Next collection from calendar events."""

from __future__ import annotations

from datetime import date

from custom_components.irabelle_waste_management.hub import _next_collection

EVENTS = [
    {"start": "2026-10-14", "summary": "Rubbish"},
    {"start": "2026-10-21", "summary": "Rubbish"},
    {"start": "2026-10-16", "summary": "Recycling"},
]


def test_keyword_and_future_only() -> None:
    assert _next_collection(EVENTS, "rubbish", date(2026, 10, 15)) == date(2026, 10, 21)
    assert _next_collection(EVENTS, "recycling", date(2026, 10, 15)) == date(2026, 10, 16)
    assert _next_collection(EVENTS, "glass", date(2026, 10, 15)) is None
    assert _next_collection(EVENTS, "", date(2026, 10, 15)) == date(2026, 10, 16)
