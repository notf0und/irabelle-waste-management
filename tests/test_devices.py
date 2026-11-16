"""Bin devices link to the hub device by its device id."""

import logging
from types import SimpleNamespace

from homeassistant.config_entries import ConfigSubentryData
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.irabelle_waste_management.const import DOMAIN
from custom_components.irabelle_waste_management.entity import (
    BinEntity,
    hub_device_info,
)


async def test_bin_device_links_to_hub(hass, caplog):
    """A bin device names the hub as its via device without deprecated arguments."""
    caplog.set_level(logging.WARNING)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Hub",
        subentries_data=[
            ConfigSubentryData(
                data={}, subentry_type="bin", title="Red bin", unique_id="x"
            )
        ],
    )
    entry.add_to_hass(hass)
    sub_id = next(iter(entry.subentries))
    reg = dr.async_get(hass)
    hub = SimpleNamespace(entry=entry, hass=hass)
    hub.device_id = reg.async_get_or_create(
        config_entry_id=entry.entry_id, **hub_device_info(hub)
    ).id
    bin_ = SimpleNamespace(subentry_id=sub_id, name="Red bin", address="AA:BB")
    ent = BinEntity(hub, bin_, "status")
    dev = reg.async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=sub_id,
        **ent._attr_device_info,
    )
    assert dev.via_device_id == hub.device_id
    assert "deprecated" not in caplog.text, caplog.text
