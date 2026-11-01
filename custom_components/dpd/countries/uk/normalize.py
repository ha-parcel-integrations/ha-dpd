"""DPD UK consumer-app parcel normalization."""

from __future__ import annotations

from datetime import date, datetime, time
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from ...const import DPD_UK_TRACKING_URL, HISTORY_MAX_EVENTS, ParcelStatus
from .fields import KNOWN_FIELDS

_LOGGER = logging.getLogger(__name__)
_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-dpd/issues/new?template=unrecognised_status.yml"
_LONDON = ZoneInfo("Europe/London")
_CLOCK = re.compile(r"\b(\d{1,2}):(\d{2})")
_SKETCH_DEPTH = 3
_END_OF_DAY = time(23, 59, 59)

_SEEN_FIELDS: set[str] = set()
_UNMAPPED_STATUSES: set[tuple[Any, Any]] = set()
_UNMAPPED_EVENTS: set[Any] = set()
_BAD_DATES: set[str] = set()

_NO_FOCUS_BOX = 0
_PICKUP_PASS = 5
_ON_WAY_TO_PICKUP_SHOP = 4
_STATUS_TYPE_MAP = {
    2: ParcelStatus.DELIVERED,  # DELIVERY_IMAGE
    3: ParcelStatus.AT_PICKUP_POINT,  # COLLECT_FROM_DEPOT
    _ON_WAY_TO_PICKUP_SHOP: ParcelStatus.IN_TRANSIT,
    6: ParcelStatus.OUT_FOR_DELIVERY,  # OUT_FOR_DELIVERY_MAP
    7: ParcelStatus.PROBLEM,  # HAS_ISSUE
    8: ParcelStatus.IN_TRANSIT,  # ESTIMATED_DATE_INSTRUCTION
}
# DPD's sentences carry times and places, so these match on a fragment.
_WORDING = (
    ("has been delivered", ParcelStatus.DELIVERED),
    ("waiting for you at home", ParcelStatus.DELIVERED),
    ("has been cancelled", ParcelStatus.PROBLEM),
    ("will be with you today", ParcelStatus.OUT_FOR_DELIVERY),
    ("on the vehicle for delivery", ParcelStatus.OUT_FOR_DELIVERY),
    ("not yet received your parcel", ParcelStatus.REGISTERED),
    ("customs", ParcelStatus.IN_TRANSIT),
    ("duties and taxes", ParcelStatus.IN_TRANSIT),
    ("on its way", ParcelStatus.IN_TRANSIT),
    ("our depot", ParcelStatus.IN_TRANSIT),
    ("delivery depot", ParcelStatus.IN_TRANSIT),
    ("sortation facility", ParcelStatus.IN_TRANSIT),
    ("in transit", ParcelStatus.IN_TRANSIT),
)
# These open a pickup-point locker or counter, so they never reach ``raw``.
_PASS_FIELDS = frozenset({"collectionCode", "collectionCodeDisplay"})


def _field_types(value: Any, prefix: str = "", depth: int = 0) -> dict[str, str]:
    """Describe a record by field paths and types, never by its content."""
    if isinstance(value, dict):
        if depth >= _SKETCH_DEPTH:
            return {prefix: "dict"}
        fields: dict[str, str] = {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            fields.update(_field_types(item, path, depth + 1))
        return fields
    # A list is a container, not a level.
    if isinstance(value, list):
        if not value:
            return {prefix: "list"}
        return _field_types(value[0], f"{prefix}[]", depth)
    return {prefix: type(value).__name__}


def _warn_new_fields(parcel: dict[str, Any]) -> None:
    """Log each field name once that the confirmed shape does not have.

    A null in the confirmed shape hides what is nested under it, so a filled
    one is what this surfaces. No value is ever logged: the record carries the
    recipient.
    """
    new = {
        path: kind
        for path, kind in _field_types(parcel).items()
        if path not in KNOWN_FIELDS and path not in _SEEN_FIELDS
    }
    if not new:
        return
    _SEEN_FIELDS.update(new)
    _LOGGER.warning(
        "DPD UK returned parcel fields not seen before — report this line at "
        "%s (fields=%s)",
        _ISSUE_URL,
        ", ".join(f"{path}:{kind}" for path, kind in sorted(new.items())),
    )


def _warn_bad_date(value: str) -> None:
    shape = f"string length={len(value)}"
    if shape in _BAD_DATES:
        return
    _BAD_DATES.add(shape)
    _LOGGER.warning(
        "DPD UK returned an unparseable date — report its shape at %s (%s)",
        _ISSUE_URL,
        shape,
    )


def _timestamp(value: Any) -> str | None:
    """Read a DPD date; one without an offset is UK civil time."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        _warn_bad_date(value)
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_LONDON)
    return parsed.isoformat()


def _clock(value: Any) -> time | None:
    match = _CLOCK.search(value) if isinstance(value, str) else None
    if match is None:
        return None
    try:
        return time(int(match[1]), int(match[2]))
    except ValueError:
        return None


def _day(text: str) -> date | None:
    """Read a day; DPD writes its dates both ISO and as day/month/year."""
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    try:
        return datetime.strptime(text[:10], "%d/%m/%Y").date()
    except ValueError:
        return None


def _window(parcel: dict[str, Any]) -> tuple[str | None, str | None]:
    """The estimated day, narrowed by its times when they make a real range."""
    value = parcel.get("estimatedDeliveryDate")
    if not isinstance(value, str) or not value.strip():
        return None, None
    day = _day(value.strip())
    if day is None:
        _warn_bad_date(value)
        return None, None
    start = _clock(parcel.get("estimatedDeliveryStartTime"))
    end = _clock(parcel.get("estimatedDeliveryEndTime"))
    if start is None or end is None or end <= start:
        start, end = time.min, _END_OF_DAY
    return (
        datetime.combine(day, start, _LONDON).isoformat(),
        datetime.combine(day, end, _LONDON).isoformat(),
    )


def _weight(parcel: dict[str, Any]) -> float | None:
    """The parcel's weight; the fields are per consignment, so only for one parcel."""
    details = parcel.get("deliveryDetails")
    if not isinstance(details, dict) or details.get("numberOfParcels") != 1:
        return None
    for key in ("verifiedWeight", "totalWeight"):
        value = details.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value > 0:
            return float(value)
    return None


def _sender(parcel: dict[str, Any]) -> str | None:
    shipper = parcel.get("shipperDetails")
    name = shipper.get("customerDisplayName") if isinstance(shipper, dict) else None
    return name if isinstance(name, str) and name else None


def _map_wording(text: Any) -> ParcelStatus | None:
    if not isinstance(text, str):
        return None
    lowered = text.lower()
    for fragment, status in _WORDING:
        if fragment in lowered:
            return status
    return None


def build_history_uk(
    events: list[dict[str, Any]], *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict[str, Any]]:
    """Build the canonical ``history``, oldest to newest.

    ``raw_status`` is the event's code rather than its sentence, which can name
    a place or a person. Only the sentence is known to map, so it sets
    ``status``; a sentence that does not map is reported by that code.
    """
    dated: list[tuple[str, dict[str, Any]]] = []
    for event in events:
        timestamp = _timestamp(event.get("eventDate"))
        if timestamp is None:
            continue
        code = event.get("eventCode")
        status = _map_wording(event.get("eventText"))
        if status is None and code not in _UNMAPPED_EVENTS:
            _UNMAPPED_EVENTS.add(code)
            _LOGGER.warning(
                "Unrecognised DPD UK parcel event — report it at %s (eventCode=%r)",
                _ISSUE_URL,
                code,
            )
        dated.append(
            (
                timestamp,
                {
                    "timestamp": timestamp,
                    "status": status or ParcelStatus.UNKNOWN,
                    "raw_status": None if code is None else str(code),
                },
            )
        )
    dated.sort(key=lambda pair: pair[0])
    return [entry for _, entry in dated[-max_events:]]


def map_parcel_status_uk(
    parcel: dict[str, Any],
    *,
    inactive: bool,
    history: list[dict[str, Any]] | None = None,
) -> ParcelStatus:
    """Resolve the status the way the app does.

    A pickup pass counts as waiting even when DPD already calls the parcel
    delivered: that flag means handed to the shop, not to the recipient.
    Delivered is decided before the status type, and the bucket only settles a
    type that says nothing.
    """
    kind = parcel.get("parcelStatusType")
    undelivered = parcel.get("undelivered") is True
    if parcel.get("collectedFromShop") is True:
        return ParcelStatus.DELIVERED
    if kind == _PICKUP_PASS:
        return ParcelStatus.AT_PICKUP_POINT
    if parcel.get("deliveredToConsumer") is True and not undelivered:
        return ParcelStatus.DELIVERED
    if kind in _STATUS_TYPE_MAP:
        return _STATUS_TYPE_MAP[kind]
    worded = _map_wording(parcel.get("trackingStatusCurrent"))
    if worded is not None:
        return worded
    for entry in reversed(history or []):
        if entry["status"] is not ParcelStatus.UNKNOWN:
            return entry["status"]
    if inactive and not undelivered:
        return ParcelStatus.DELIVERED
    if kind == _NO_FOCUS_BOX and not inactive:
        return ParcelStatus.IN_TRANSIT
    seen = (kind, undelivered)
    if seen not in _UNMAPPED_STATUSES:
        _UNMAPPED_STATUSES.add(seen)
        _LOGGER.warning(
            "Unrecognised DPD UK status — report it at %s "
            "(parcelStatusType=%r, undelivered=%r, inactive=%r)",
            _ISSUE_URL,
            kind,
            undelivered,
            inactive,
        )
    return ParcelStatus.UNKNOWN


def _delivered_at(parcel: dict[str, Any]) -> str | None:
    details = parcel.get("deliveryDetails")
    pod = details.get("podDetails") if isinstance(details, dict) else None
    pod_date = pod.get("podDate") if isinstance(pod, dict) else None
    return _timestamp(pod_date or parcel.get("lastConfirmDate"))


def normalize_parcel_uk(
    parcel: dict[str, Any],
    *,
    inactive: bool,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Normalize one received-parcel record from the UK app backend."""
    _warn_new_fields(parcel)
    barcode = parcel.get("parcelNumber")
    code = parcel.get("parcelCode")
    status = map_parcel_status_uk(parcel, inactive=inactive, history=history)
    delivered = status is ParcelStatus.DELIVERED
    planned_from, planned_to = (None, None) if delivered else _window(parcel)
    text = parcel.get("trackingStatusCurrent")
    return {
        "carrier": "DPD",
        "barcode": barcode if isinstance(barcode, str) and barcode else None,
        "sender": _sender(parcel),
        "receiver": None,
        "status": status,
        "raw_status": text if isinstance(text, str) and text else None,
        "delivered": delivered,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "delivered_at": _delivered_at(parcel) if delivered else None,
        "pickup": bool(
            parcel.get("parcelStatusType") in (_ON_WAY_TO_PICKUP_SHOP, _PICKUP_PASS)
            or parcel.get("pickupLocationCode")
        ),
        "pickup_point": None,
        "weight": _weight(parcel),
        "dimensions": None,
        "history": history,
        "url": (
            f"{DPD_UK_TRACKING_URL}/{code}" if isinstance(code, str) and code else None
        ),
        "raw": {key: value for key, value in parcel.items() if key not in _PASS_FIELDS},
    }
