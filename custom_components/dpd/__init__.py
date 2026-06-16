"""DPD custom component for Home Assistant."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DpdApiClient, DpdAuthError
from .const import CONF_BU, DEFAULT_BU, PLATFORMS
from .coordinator import DpdCoordinator

_LOGGER = logging.getLogger(__name__)


@dataclass
class DpdData:
    """Runtime data attached to a DPD config entry."""

    client: DpdApiClient
    coordinator: DpdCoordinator


type DpdConfigEntry = ConfigEntry[DpdData]


async def async_setup_entry(hass: HomeAssistant, entry: DpdConfigEntry) -> bool:
    """Set up DPD from a config entry."""
    session = async_get_clientsession(hass)
    client = DpdApiClient(
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        session,
        bu=entry.data.get(CONF_BU, DEFAULT_BU),
    )

    try:
        await client.async_login()
    except DpdAuthError as exc:
        raise ConfigEntryAuthFailed("DPD authentication failed") from exc
    except aiohttp.ClientError as exc:
        raise ConfigEntryNotReady("Unable to connect to DPD") from exc

    coordinator = DpdCoordinator(hass, client, entry)
    entry.runtime_data = DpdData(client=client, coordinator=coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_options))
    return True


async def _async_update_options(hass: HomeAssistant, entry: DpdConfigEntry) -> None:
    """Refresh the coordinator immediately when options are changed."""
    await entry.runtime_data.coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: DpdConfigEntry) -> bool:
    """Unload a DPD config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
