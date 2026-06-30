"""Tests for the DPD refresh button."""
from unittest.mock import AsyncMock, patch

import pytest

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers import entity_registry as er

from custom_components.dpd.const import (
    CONF_BU,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_BU,
    DOMAIN,
)

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


@pytest.mark.asyncio
async def test_refresh_button_forces_a_poll(hass):
    """Pressing the refresh button re-polls the coordinator."""
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
        ) as mock_get_parcels,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "button", DOMAIN, f"{entry.entry_id}_refresh"
        )
        assert entity_id is not None
        assert hass.states.get(entity_id) is not None

        calls_before = mock_get_parcels.await_count

        await hass.services.async_call(
            "button", "press", {"entity_id": entity_id}, blocking=True
        )
        await hass.async_block_till_done()

    assert mock_get_parcels.await_count > calls_before
