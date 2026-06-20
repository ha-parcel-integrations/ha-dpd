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
    KNOWN_DESCRIPTIONS,
    POLL_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)

# Descriptions we have already info-logged once in this HA session, so
# repeated polls do not flood the log with the same "new status" message.
_unknown_descriptions_logged: set[str] = set()


def _description(shipment: dict) -> str | None:
    return (shipment.get("status") or {}).get("description")


def log_unknown_descriptions(shipments: list[dict]) -> None:
    """Info-log any status.description we have not catalogued yet, once per value.

    Lets us extend ``KNOWN_DESCRIPTIONS`` as new lifecycle stages surface
    in real accounts without spamming the log on every poll.
    """
    for shipment in shipments:
        description = _description(shipment)
        if (
            description
            and description not in KNOWN_DESCRIPTIONS
            and description not in _unknown_descriptions_logged
        ):
            _unknown_descriptions_logged.add(description)
            _LOGGER.info(
                "DPD parcel status.description not yet catalogued: %r. "
                "Please open an issue so we can add it to KNOWN_DESCRIPTIONS.",
                description,
            )


def filter_active_shipments(shipments: list[dict]) -> list[dict]:
    """Return shipments that have not yet reached the DELIVERED state."""
    return [s for s in shipments if _description(s) != DELIVERED_DESCRIPTION]


def filter_delivered_shipments(shipments: list[dict]) -> list[dict]:
    """Return shipments in the DELIVERED state."""
    return [s for s in shipments if _description(s) == DELIVERED_DESCRIPTION]


def shipment_planned_dt(shipment: dict) -> datetime | None:
    """Return the planned delivery datetime for an active shipment, or ``None``.

    Prefers the Follow My Parcel window when the coordinator has
    enriched the shipment with it (``fmpDeliveryDateAndTime.deliveryDate``
    combined with ``timeRange.from``) — this is the precise hour window
    DPD shows on its tracking page on the day of delivery.

    Falls back to plain ``deliveryDate`` at midnight in the timezone
    reported by ``status.eventDateAndTimeZoneId`` (or UTC) — the only
    information available before a delivery window is scheduled.
    """
    tz_id = (shipment.get("status") or {}).get("eventDateAndTimeZoneId")
    tz: timezone | ZoneInfo = timezone.utc
    if tz_id:
        try:
            tz = ZoneInfo(tz_id)
        except Exception:  # noqa: BLE001 - bad tz string from API
            tz = timezone.utc

    fmp = shipment.get("fmpDeliveryDateAndTime") or {}
    fmp_date = fmp.get("deliveryDate")
    fmp_from = (fmp.get("timeRange") or {}).get("from")
    if fmp_date and fmp_from:
        try:
            return datetime.fromisoformat(f"{fmp_date}T{fmp_from}").replace(tzinfo=tz)
        except ValueError:
            pass

    date_str = shipment.get("deliveryDate")
    if not date_str:
        return None
    try:
        d = datetime.fromisoformat(date_str)
    except ValueError:
        return None
    return d.replace(tzinfo=tz)


def fmp_hashcode(shipment: dict) -> str | None:
    """Pluck the Follow My Parcel hashcode off a shipment, when present.

    DPD lists ``availableActions.FOLLOW_MY_PARCEL`` as an array with at
    most one entry; the hashcode is what the FMP authenticate endpoint
    expects as credentials. Returns ``None`` for shipments that have not
    yet been scheduled into the FMP system (typically anything before
    the day of delivery).
    """
    actions = shipment.get("availableActions") or {}
    fmp_actions = actions.get("FOLLOW_MY_PARCEL") or []
    if not fmp_actions:
        return None
    hashcode = fmp_actions[0].get("hashcode")
    return hashcode if isinstance(hashcode, str) and hashcode else None


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
            _LOGGER.error("DPD authentication failed: %s", err)
            raise ConfigEntryAuthFailed("DPD authentication failed") from err
        except (DpdApiError, aiohttp.ClientError) as err:
            _LOGGER.warning("DPD parcels endpoint unreachable: %s", err)
            raise UpdateFailed(f"DPD error: {err}") from err

        incoming = payload.get("incomingShipments") or []
        outgoing = payload.get("sendingShipments") or []

        log_unknown_descriptions(incoming + outgoing)

        incoming_active = filter_active_shipments(incoming)
        incoming_delivered = self._apply_delivered_filter(
            filter_delivered_shipments(incoming)
        )
        outgoing_active = filter_active_shipments(outgoing)

        await self._enrich_with_fmp(incoming_active)

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

    async def _enrich_with_fmp(self, shipments: list[dict]) -> None:
        """In-place: add ``fmpDeliveryDateAndTime`` to shipments that expose FMP.

        Only shipments with an ``availableActions.FOLLOW_MY_PARCEL`` action
        are queried — typically those out for delivery today. Failures are
        swallowed by the API client (returns ``None``) so a broken FMP
        call never breaks the parcels poll.
        """
        for shipment in shipments:
            hashcode = fmp_hashcode(shipment)
            if not hashcode:
                continue
            window = await self._client.async_fmp_delivery_window(hashcode)
            if window:
                shipment["fmpDeliveryDateAndTime"] = window

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
