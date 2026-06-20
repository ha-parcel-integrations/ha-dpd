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
    STATUS_AT_DELIVERY_CENTER,
    STATUS_DELIVERED,
    STATUS_IN_TRANSIT,
    STATUS_ORDER_CREATED,
    STATUS_PARCEL_HANDED,
    STATUS_PARCEL_OUT_FOR_DELIVERY,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

# DPD status.description → canonical ParcelStatus. Every value in
# KNOWN_DESCRIPTIONS is mapped here; anything else falls back to
# ParcelStatus.UNKNOWN and triggers a one-shot info log via
# log_unknown_descriptions.
_DESCRIPTION_MAP: dict[str, ParcelStatus] = {
    STATUS_ORDER_CREATED: ParcelStatus.REGISTERED,
    STATUS_PARCEL_HANDED: ParcelStatus.IN_TRANSIT,
    STATUS_IN_TRANSIT: ParcelStatus.IN_TRANSIT,
    STATUS_AT_DELIVERY_CENTER: ParcelStatus.IN_TRANSIT,
    STATUS_PARCEL_OUT_FOR_DELIVERY: ParcelStatus.OUT_FOR_DELIVERY,
    STATUS_DELIVERED: ParcelStatus.DELIVERED,
}

# Descriptions we have already info-logged once in this HA session, so
# repeated polls do not flood the log with the same "new status" message.
_unknown_descriptions_logged: set[str] = set()


def _description(shipment: dict) -> str | None:
    return (shipment.get("status") or {}).get("description")


def map_parcel_status(parcel: dict) -> ParcelStatus:
    """Map a raw DPD parcel to a canonical :class:`ParcelStatus`.

    Reads ``status.description`` and looks it up in ``_DESCRIPTION_MAP``;
    unknown values (or a missing status field) fall back to
    ``ParcelStatus.UNKNOWN``. New raw values are surfaced separately via
    :func:`log_unknown_descriptions` so the map can be extended.
    """
    description = _description(parcel)
    return _DESCRIPTION_MAP.get(description or "", ParcelStatus.UNKNOWN)


def log_unknown_descriptions(shipments: list[dict]) -> None:
    """Info-log any status.description we have not mapped yet, once per value.

    Lets us extend ``_DESCRIPTION_MAP`` as new lifecycle stages surface
    in real accounts without spamming the log on every poll. Anything
    not in the map is reported as ``ParcelStatus.UNKNOWN`` until it is.
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
                "DPD status.description %r is not in _DESCRIPTION_MAP — "
                "will be reported as ParcelStatus.UNKNOWN. Please open "
                "an issue so we can add the mapping.",
                description,
            )


def filter_active_shipments(shipments: list[dict]) -> list[dict]:
    """Return shipments that have not yet reached the DELIVERED state."""
    return [s for s in shipments if _description(s) != DELIVERED_DESCRIPTION]


def filter_delivered_shipments(shipments: list[dict]) -> list[dict]:
    """Return shipments in the DELIVERED state."""
    return [s for s in shipments if _description(s) == DELIVERED_DESCRIPTION]


def _tracking_url(parcel: dict) -> str | None:
    """Build the DPD tracking URL for a parcel, or ``None`` when no parcelNumber.

    The ``/nl/`` segment is hardcoded while only DPD-NL is supported as
    a business unit — see CLAUDE.md. When more BUs are added, map each
    to its tracking-page country code.
    """
    parcel_number = parcel.get("parcelNumber")
    if not parcel_number:
        return None
    return (
        f"https://www.dpdgroup.com/nl/mydpd/my-parcels/search?"
        f"parcelNumber={parcel_number}"
    )


def normalize_parcel(parcel: dict) -> dict:
    """Return a carrier-agnostic parcel dict with the DPD payload under ``raw``.

    Mirrors the shape every other carrier integration (DHL, PostNL)
    publishes, so the parcel aggregator and cross-carrier dashboards
    can read parcels the same way regardless of source. The original
    DPD shipment object stays available under ``raw``.

    ``planned_from`` / ``planned_to`` are read from the
    ``plannedDeliveryFrom`` / ``plannedDeliveryTo`` annotations added
    by :func:`annotate_planned_delivery` (FMP window when available,
    full-day fallback otherwise), and cleared for delivered parcels
    where ``delivered_at`` carries the actual moment instead.
    """
    description = _description(parcel)
    delivered = description == DELIVERED_DESCRIPTION
    delivered_at: str | None = None
    if delivered:
        dt = shipment_delivery_dt(parcel)
        delivered_at = dt.isoformat() if dt else None
    is_pickup = (parcel.get("status") or {}).get("deliveryType") == "PARCELSHOP"
    return {
        "carrier": "DPD",
        "barcode": parcel.get("parcelNumber"),
        "sender": parcel.get("senderName"),
        "status": map_parcel_status(parcel),
        "raw_status": description,
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": None if delivered else parcel.get("plannedDeliveryFrom"),
        "planned_to": None if delivered else parcel.get("plannedDeliveryTo"),
        "pickup": is_pickup,
        "pickup_point": None,
        "url": _tracking_url(parcel),
        "raw": parcel,
    }


def shipment_planned_window(shipment: dict) -> tuple[datetime | None, datetime | None]:
    """Return the planned ``(from, to)`` delivery window for a shipment.

    When DPD has scheduled a precise Follow My Parcel window (typically on
    the day a parcel is out for delivery) both bounds carry the hour
    range, e.g. ``(10:34, 11:34)`` on the delivery date.

    Otherwise — and only ``deliveryDate`` is known — the window spans the
    whole calendar day in the parcel's local timezone (start of day,
    ``23:59:59`` end of day). Returns ``(None, None)`` when even the
    date is missing or unparseable.
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
    time_range = fmp.get("timeRange") or {}
    fmp_from = time_range.get("from")
    fmp_to = time_range.get("to")
    if fmp_date and fmp_from and fmp_to:
        try:
            return (
                datetime.fromisoformat(f"{fmp_date}T{fmp_from}").replace(tzinfo=tz),
                datetime.fromisoformat(f"{fmp_date}T{fmp_to}").replace(tzinfo=tz),
            )
        except ValueError:
            pass

    date_str = shipment.get("deliveryDate")
    if not date_str:
        return (None, None)
    try:
        d = datetime.fromisoformat(date_str)
    except ValueError:
        return (None, None)
    start = d.replace(tzinfo=tz)
    end = d.replace(hour=23, minute=59, second=59, tzinfo=tz)
    return (start, end)


def shipment_planned_dt(shipment: dict) -> datetime | None:
    """Return the start of the planned delivery window, or ``None``.

    Thin wrapper around :func:`shipment_planned_window` that keeps the
    "start time" semantics callers (e.g. the next-delivery sensor) rely
    on for sorting.
    """
    return shipment_planned_window(shipment)[0]


def annotate_planned_delivery(shipment: dict) -> None:
    """In-place: add ``plannedDeliveryFrom`` / ``plannedDeliveryTo`` to a shipment.

    Both values are ISO 8601 strings with timezone offset, or ``None``
    when the shipment carries no parseable date information. Surfacing
    them top-level means every sensor that exposes the raw shipment dict
    gets a ready-to-template ``from`` / ``to`` pair without callers
    having to dig into ``fmpDeliveryDateAndTime`` or recompute midnight
    fallbacks.
    """
    start, end = shipment_planned_window(shipment)
    shipment["plannedDeliveryFrom"] = start.isoformat() if start else None
    shipment["plannedDeliveryTo"] = end.isoformat() if end else None


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

        for shipment in (*incoming_active, *incoming_delivered, *outgoing_active):
            annotate_planned_delivery(shipment)

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
            "incoming_active": [normalize_parcel(p) for p in incoming_active],
            "incoming_delivered": [normalize_parcel(p) for p in incoming_delivered],
            "outgoing_active": [normalize_parcel(p) for p in outgoing_active],
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
