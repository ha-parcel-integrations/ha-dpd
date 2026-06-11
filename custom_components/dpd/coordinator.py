"""Coordinator for the DPD integration."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DpdApiClient, DpdApiError, DpdAuthError
from .const import DOMAIN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


class DpdCoordinator(DataUpdateCoordinator[dict[str, list[dict]]]):
    """Coordinator that polls the DPD parcels API on a fixed schedule."""

    def __init__(
        self, hass: HomeAssistant, client: DpdApiClient, entry: ConfigEntry
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=POLL_INTERVAL),
        )
        self._client = client
        self._entry = entry

    async def _async_update_data(self) -> dict[str, list[dict]]:
        try:
            payload = await self._client.async_get_parcels()
        except DpdAuthError as err:
            raise ConfigEntryAuthFailed("DPD authentication failed") from err
        except (DpdApiError, aiohttp.ClientError) as err:
            raise UpdateFailed(f"DPD error: {err}") from err

        # TODO: split into active/delivered once the parcel object shape is known.
        # See DHL's ACTIVE_CATEGORIES + filter_active_parcels for the pattern.
        incoming = payload.get("incomingShipments") or []
        outgoing = payload.get("sendingShipments") or []

        _LOGGER.debug(
            "DPD parcels fetched: %d incoming, %d outgoing",
            len(incoming),
            len(outgoing),
        )
        if incoming or outgoing:
            _LOGGER.debug("DPD raw parcels payload: %s", payload)

        return {"incoming": incoming, "outgoing": outgoing}
