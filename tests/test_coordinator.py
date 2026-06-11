"""Tests for the DPD coordinator response parsing and error handling."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dpd.api import DpdApiError, DpdAuthError
from custom_components.dpd.coordinator import DpdCoordinator


def _mock_entry() -> MagicMock:
    entry = MagicMock()
    entry.options = {}
    return entry


async def test_coordinator_splits_incoming_and_outgoing(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value={
        "incomingShipments": [{"id": "A"}, {"id": "B"}],
        "sendingShipments": [{"id": "C"}],
    })

    coordinator = DpdCoordinator(hass, client, _mock_entry())
    result = await coordinator._async_update_data()

    assert result == {
        "incoming": [{"id": "A"}, {"id": "B"}],
        "outgoing": [{"id": "C"}],
    }


async def test_coordinator_handles_empty_response(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value={
        "incomingShipments": [],
        "sendingShipments": [],
    })

    coordinator = DpdCoordinator(hass, client, _mock_entry())
    result = await coordinator._async_update_data()

    assert result == {"incoming": [], "outgoing": []}


async def test_coordinator_handles_missing_keys(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value={})

    coordinator = DpdCoordinator(hass, client, _mock_entry())
    result = await coordinator._async_update_data()

    assert result == {"incoming": [], "outgoing": []}


async def test_coordinator_raises_config_entry_auth_failed_on_auth_error(hass):
    from homeassistant.exceptions import ConfigEntryAuthFailed

    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=DpdAuthError("bad creds"))

    coordinator = DpdCoordinator(hass, client, _mock_entry())

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_coordinator_raises_update_failed_on_api_error(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=DpdApiError(500))

    coordinator = DpdCoordinator(hass, client, _mock_entry())

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
