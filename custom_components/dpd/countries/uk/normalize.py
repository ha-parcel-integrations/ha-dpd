"""DPD UK consumer-app parcel normalization (coarse, pending a real parcel)."""

from __future__ import annotations

import json
import logging
from typing import Any

from ...const import DPD_UK_TRACKING_URL, ParcelStatus

_LOGGER = logging.getLogger(__name__)
_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-dpd/issues/new?template=unrecognised_status.yml"
_SEEN_SHAPES: set[str] = set()
_UNMAPPED_STATUSES: set[str] = set()
# ASSUMED: the spelling of the per-parcel status field is not yet seen on the
# wire; these candidates are tried before any other status-like key.
_STATUS_KEYS = ("status", "parcelStatus", "statusCode")
_SKETCH_DEPTH = 3


def _sketch(value: Any, depth: int = 0) -> Any:
    """Describe a value by its field names and types, never by its content."""
    if isinstance(value, dict):
        if depth >= _SKETCH_DEPTH:
            return "{...}"
        return {str(key): _sketch(item, depth + 1) for key, item in value.items()}
    # A list is a container, not a level: the event records inside it are
    # where the status vocabulary will be.
    if isinstance(value, list):
        return [_sketch(value[0], depth)] if value else []
    return type(value).__name__


def _warn_shape(parcel: dict[str, Any]) -> None:
    """Log each distinct parcel shape once.

    One report has to be enough to finish the normalizer, so the nested field
    names and their types go in the log too -- but no value ever does, because
    the record carries the recipient.
    """
    sketch = json.dumps(_sketch(parcel), sort_keys=True)
    if sketch in _SEEN_SHAPES:
        return
    _SEEN_SHAPES.add(sketch)
    _LOGGER.warning(
        "DPD UK returned a parcel shape not seen before — report this line at "
        "%s (shape=%s)",
        _ISSUE_URL,
        sketch,
    )


def _status_candidates(parcel: dict[str, Any]) -> list[str]:
    """Every key that could hold the status, the assumed spellings first."""
    rest = [
        key
        for key in parcel
        if isinstance(key, str)
        and key not in _STATUS_KEYS
        and ("status" in key.lower() or "state" in key.lower())
        # A timestamp beside the status is not the vocabulary.
        and not any(word in key.lower() for word in ("date", "time", "updated"))
    ]
    return [*_STATUS_KEYS, *sorted(rest)]


def _raw_status(parcel: dict[str, Any]) -> str | None:
    for key in _status_candidates(parcel):
        value = parcel.get(key)
        if isinstance(value, str) and value:
            seen = f"{key}={value}"
            if seen not in _UNMAPPED_STATUSES:
                _UNMAPPED_STATUSES.add(seen)
                _LOGGER.warning(
                    "Unrecognised DPD UK status — report it at %s (%s=%r)",
                    _ISSUE_URL,
                    key,
                    value,
                )
            return value
    return None


def normalize_parcel_uk(
    parcel: dict[str, Any], *, inactive: bool, include_history: bool = False
) -> dict[str, Any]:
    """Normalize one received-parcel record from the UK app backend.

    Only the bucket decides the status: ``inactive`` is delivered and
    ``active`` is in progress, until the per-event vocabulary is captured.
    """
    _warn_shape(parcel)
    barcode = parcel.get("parcelNumber")  # ASSUMED field name
    code = parcel.get("parcelCode")  # ASSUMED field name
    status = ParcelStatus.DELIVERED if inactive else ParcelStatus.IN_TRANSIT
    return {
        "carrier": "DPD",
        "barcode": barcode if isinstance(barcode, str) and barcode else None,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": _raw_status(parcel),
        "delivered": inactive,
        "planned_from": None,
        "planned_to": None,
        "delivered_at": None,
        "pickup": False,
        "pickup_point": None,
        "weight": None,
        "dimensions": None,
        "history": [] if include_history else None,
        "url": (
            f"{DPD_UK_TRACKING_URL}/{code}" if isinstance(code, str) and code else None
        ),
        "raw": parcel,
    }
