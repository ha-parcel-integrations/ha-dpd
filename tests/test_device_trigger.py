"""Tests for the DPD device triggers."""
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.components import automation
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component

from custom_components.dpd.const import (
    CONF_BU,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_BU,
    DOMAIN,
)
from custom_components.dpd.device_trigger import TRIGGER_TYPES, async_get_triggers

_ENTRY_DATA = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_BU: DEFAULT_BU,
}


def _add_entry(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DEFAULT_BU}:{_ENTRY_DATA[CONF_EMAIL]}",
        data=_ENTRY_DATA,
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _setup_and_get_device_id(hass):
    """Set up the integration and return the account's device id."""
    entry = _add_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdApiClient.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcels",
            new=AsyncMock(
                return_value={"incomingShipments": [], "sendingShipments": []}
            ),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, entry.entry_id)}
    )
    assert device is not None
    return device.id


@pytest.mark.asyncio
async def test_get_triggers_lists_all_parcel_events(hass):
    """async_get_triggers returns one trigger per parcel event for the device."""
    device_id = await _setup_and_get_device_id(hass)

    triggers = await async_get_triggers(hass, device_id)

    assert {t["type"] for t in triggers} == TRIGGER_TYPES
    assert {
        "outgoing_parcel_status_changed",
        "outgoing_parcel_delivered",
    } <= TRIGGER_TYPES
    assert all(t["domain"] == DOMAIN for t in triggers)
    assert all(t["device_id"] == device_id for t in triggers)


@pytest.mark.asyncio
async def test_device_trigger_fires_automation(hass):
    """A device-trigger automation fires when the matching event is dispatched."""
    device_id = await _setup_and_get_device_id(hass)

    assert await async_setup_component(
        hass,
        automation.DOMAIN,
        {
            automation.DOMAIN: {
                "trigger": {
                    "platform": "device",
                    "domain": DOMAIN,
                    "device_id": device_id,
                    "type": "parcel_status_changed",
                },
                "action": {"event": "dpd_test_fired"},
            }
        },
    )
    await hass.async_block_till_done()

    fired: list = []
    hass.bus.async_listen("dpd_test_fired", lambda e: fired.append(e))

    hass.bus.async_fire(
        f"{DOMAIN}_parcel_status_changed",
        {"barcode": "A", "device_id": device_id},
    )
    await hass.async_block_till_done()
    assert len(fired) == 1

    hass.bus.async_fire(
        f"{DOMAIN}_parcel_status_changed",
        {"barcode": "B", "device_id": "some-other-device"},
    )
    await hass.async_block_till_done()
    assert len(fired) == 1
