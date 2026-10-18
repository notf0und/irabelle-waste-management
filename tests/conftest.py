"""Shared fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a beacon configuration captured from a real beacon."""
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture
def k4p() -> dict[str, Any]:
    """KKM K4P (firmware V6.73) configuration as read over Bluetooth."""
    return load_fixture("k4p.json")


@pytest.fixture
def bc04p() -> dict[str, Any]:
    """KKM BC04P (firmware V6.65) configuration as read over Bluetooth."""
    return load_fixture("bc04p.json")
