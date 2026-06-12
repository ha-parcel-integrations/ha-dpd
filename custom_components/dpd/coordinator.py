"""Coordinator for the DPD integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DpdApiClient, DpdApiError, DpdAuthError
from .const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_DELIVERED_FILTER_AMOUNT,
    DEFAULT_DELIVERED_FILTER_TYPE,
    DELIVERED_DESCRIPTION,
    DOMAIN,
    POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _description(shipment: dict) -> str | None:
    return (shipment.get("status") or {}).get("description")


def filter_active_shipments(shipments: list[dict]) -> list[dict]:
    """Return shipments that have not yet reached the DELIVERED state."""
    return [s for s in shipments if _description(s) != DELIVERED_DESCRIPTION]


def filter_delivered_shipments(shipments: list[dict]) -> list[dict]:
    """Return shipments in the DELIVERED state."""
    return [s for s in shipments if _description(s) == DELIVERED_DESCRIPTION]


def shipment_delivery_dt(shipment: dict) -> datetime | None:
    """Return the delivery datetime of a shipment, or ``None`` if unknown.

    Prefers ``status.eventDateAndTime`` (naive ISO 8601) combined with
    ``status.eventDateAndTimeZoneId`` (IANA timezone). Falls back to the
    plain ``deliveryDate`` (date) at start-of-day UTC.
    """
    status = shipment.get("status") or {}
    moment = status.get("eventDateAndTime")
    if moment:
        try:
            dt = datetime.fromisoformat(moment.replace("Z", "+00:00"))
        except ValueError:
            dt = None
        if dt is not None:
            if dt.tzinfo is None:
                tz_id = status.get("eventDateAndTimeZoneId")
                tz: timezone | ZoneInfo = timezone.utc
                if tz_id:
                    try:
                        tz = ZoneInfo(tz_id)
                    except Exception:  # noqa: BLE001 - bad tz string from API
                        tz = timezone.utc
                dt = dt.replace(tzinfo=tz)
            return dt

    date_str = shipment.get("deliveryDate")
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(date_str)
    except ValueError:
        return None
    return d.replace(tzinfo=timezone.utc)


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

        incoming = payload.get("incomingShipments") or []
        outgoing = payload.get("sendingShipments") or []

        incoming_active = filter_active_shipments(incoming)
        incoming_delivered = self._apply_delivered_filter(
            filter_delivered_shipments(incoming)
        )
        outgoing_active = filter_active_shipments(outgoing)

        _LOGGER.debug(
            "DPD shipments fetched: %d incoming (%d active, %d delivered shown), "
            "%d outgoing (%d active)",
            len(incoming),
            len(incoming_active),
            len(incoming_delivered),
            len(outgoing),
            len(outgoing_active),
        )
        if incoming or outgoing:
            _LOGGER.debug("DPD raw parcels payload: %s", payload)

        return {
            "incoming_active": incoming_active,
            "incoming_delivered": incoming_delivered,
            "outgoing_active": outgoing_active,
        }

    def _apply_delivered_filter(self, shipments: list[dict]) -> list[dict]:
        """Trim the delivered list according to the configured options."""
        options = self._entry.options
        filter_type = options.get(
            CONF_DELIVERED_FILTER_TYPE, DEFAULT_DELIVERED_FILTER_TYPE
        )
        filter_amount = int(
            options.get(CONF_DELIVERED_FILTER_AMOUNT, DEFAULT_DELIVERED_FILTER_AMOUNT)
        )

        if filter_type == "days":
            cutoff = datetime.now(timezone.utc) - timedelta(days=filter_amount)
            return [
                s for s in shipments
                if (dt := shipment_delivery_dt(s)) is None or dt >= cutoff
            ]

        return shipments[:filter_amount]
