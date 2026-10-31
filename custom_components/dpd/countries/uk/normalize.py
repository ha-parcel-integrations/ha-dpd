"""DPD UK consumer-app parcel normalization (coarse, pending a real parcel)."""

from __future__ import annotations

import logging
from typing import Any

from ...const import DPD_UK_TRACKING_URL, ParcelStatus

_LOGGER = logging.getLogger(__name__)
_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-dpd/issues/new?template=unrecognised_status.yml"
_SEEN_SHAPES: set[frozenset[str]] = set()
_UNMAPPED_STATUSES: set[str] = set()
# ASSUMED: the spelling of the per-parcel status field is not yet seen on the
# wire; these candidates only feed ``raw_status`` and the WARNING collector.
_STATUS_KEYS = ("status", "parcelStatus", "statusCode")


def _warn_shape(parcel: dict[str, Any]) -> None:
    """Log each distinct key set once (key names only, never values)."""
    keys = frozenset(str(key) for key in parcel)
    if keys in _SEEN_SHAPES:
        return
    _SEEN_SHAPES.add(keys)
    _LOGGER.warning(
        "DPD UK returned a parcel shape not seen before — report its keys at "
        "%s (keys=%s)",
        _ISSUE_URL,
        sorted(keys),
    )


def _raw_status(parcel: dict[str, Any]) -> str | None:
    # ASSUMED field names, see _STATUS_KEYS.
    for key in _STATUS_KEYS:
        value = parcel.get(key)
        if isinstance(value, str) and value:
            if value not in _UNMAPPED_STATUSES:
                _UNMAPPED_STATUSES.add(value)
                _LOGGER.warning(
                    "Unrecognised DPD UK status — report it at %s (status=%r)",
                    _ISSUE_URL,
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
