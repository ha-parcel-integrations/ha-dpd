"""DPD Polska parcel normalization."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ...const import ParcelStatus

_LOGGER = logging.getLogger(__name__)
_UNKNOWN: set[str] = set()
_UNEXPECTED_SHAPES: set[str] = set()
_BAD_TIMESTAMPS: set[str] = set()
_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-dpd/issues/new?template=unrecognised_status.yml"
_STATUS_MAP = {
    "READY_TO_SEND": ParcelStatus.REGISTERED,
    "RECEIVED_FROM_SENDER": ParcelStatus.IN_TRANSIT, "SENT": ParcelStatus.IN_TRANSIT, "IN_TRANSPORT": ParcelStatus.IN_TRANSIT, "RECEIVED_IN_DEPOT": ParcelStatus.IN_TRANSIT, "REDIRECTED": ParcelStatus.IN_TRANSIT, "RESCHEDULED": ParcelStatus.IN_TRANSIT,
    "HANDED_OVER_FOR_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,
    "READY_TO_PICK_UP": ParcelStatus.AT_PICKUP_POINT, "SELF_PICKUP": ParcelStatus.AT_PICKUP_POINT, "HARD_RESERVED": ParcelStatus.AT_PICKUP_POINT,
    "DELIVERED": ParcelStatus.DELIVERED, "PICKED_UP": ParcelStatus.DELIVERED,
    "RETURNED_TO_SENDER": ParcelStatus.RETURNING, "EXPIRED_PICKUP": ParcelStatus.RETURNING,
    "UNSUCCESSFUL_DELIVERY": ParcelStatus.PROBLEM,
}

def map_parcel_status_pl(raw: str | None) -> ParcelStatus:
    """Map Poland's observed status vocabulary to the parcel contract."""
    status = _STATUS_MAP.get(raw or "", ParcelStatus.UNKNOWN)
    if status is ParcelStatus.UNKNOWN and raw not in _UNKNOWN:
        _UNKNOWN.add(raw or "<missing>")
        _LOGGER.warning(
            "Unrecognised DPD Poland status — report it at %s (status=%r)",
            _ISSUE_URL,
            raw,
        )
    return status

def _timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
    except ValueError:
        shape = f"string length={len(value)} separators={sorted(set(c for c in value if not c.isalnum()))!r}"
        if shape not in _BAD_TIMESTAMPS:
            _BAD_TIMESTAMPS.add(shape)
            _LOGGER.warning(
                "DPD Poland returned an unparseable timestamp — report its "
                "shape at %s (%s)",
                _ISSUE_URL,
                shape,
            )
        return None


def _warn_unexpected_shape(path: str, value: Any) -> None:
    """Log an unknown payload shape once, never its potentially private value."""
    description = f"{path}: {type(value).__name__}"
    if description in _UNEXPECTED_SHAPES:
        return
    _UNEXPECTED_SHAPES.add(description)
    _LOGGER.warning(
        "DPD Poland returned an unexpected payload shape — report it at %s "
        "(%s)",
        _ISSUE_URL,
        description,
    )


def _status_timestamp(status: dict[str, Any]) -> Any:
    """Read all timestamp spellings present in the Android API models."""
    return status.get("date") or status.get("create_time") or status.get("createTime")

def normalize_parcel_pl(
    parcel: dict[str, Any], *, include_history: bool = False
) -> dict[str, Any]:
    """Normalize one DPD Polska receiver-inbox record."""
    main_status = parcel.get("main_status")
    if main_status is not None and not isinstance(main_status, dict):
        _warn_unexpected_shape("main_status", main_status)
    raw_status = (main_status or {}).get("status") if isinstance(main_status, dict) else None
    delivery = parcel.get("delivery") or {}
    if delivery and not isinstance(delivery, dict):
        _warn_unexpected_shape("delivery", delivery)
        delivery = {}
    statuses = parcel.get("statuses")
    if statuses is not None and not isinstance(statuses, list):
        _warn_unexpected_shape("statuses", statuses)
        statuses = []
    history = (
        [
            {
                "timestamp": _timestamp(_status_timestamp(item)),
                "status": map_parcel_status_pl(item.get("status")),
                "raw_status": item.get("status"),
            }
            for item in statuses or []
            if isinstance(item, dict)
        ]
        if include_history
        else None
    )
    status = map_parcel_status_pl(raw_status)
    return {
        "carrier": "DPD Polska", "barcode": parcel.get("waybill"), "sender": ((parcel.get("sender") or {}).get("name")), "receiver": None,
        "status": status, "raw_status": raw_status, "delivered": status is ParcelStatus.DELIVERED,
        "planned_from": _timestamp(delivery.get("planned_delivery_date") or parcel.get("planned_delivery_date")), "planned_to": None,
        "delivered_at": _timestamp(delivery.get("delivered_datetime")), "pickup": False, "pickup_point": None, "weight": None, "dimensions": None,
        "history": history, "url": None,
        "raw": parcel,
    }
