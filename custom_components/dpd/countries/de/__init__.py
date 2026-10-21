"""DPD Germany: status map and ``normalize_parcel_de`` for the web account.

The parcel list gives a one-sentence status and an icon number; the selected
parcel's panel gives five fixed milestones, each a localised text and a day
(``DD.MM.``, no year, no time). Only the milestones are a closed structure, so
they decide the status; the sentence and the icon are the fallbacks, and the
icon table holds observed values only.

Only a handful of parcels have been seen, so every wording this module does
not recognise is reported once by a WARNING instead of being guessed.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
import logging
import re
from typing import Any
from zoneinfo import ZoneInfo

from ...const import DPD_DE_TRACKING_URL, HISTORY_MAX_EVENTS, ParcelStatus
from .page import SLOTS
from .session import DpdDeSession

_LOGGER = logging.getLogger(__name__)
_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-dpd/issues/new"
    "?template=unrecognised_status.yml"
)

# The account has no other market, so the milestone days are Germany-local.
_DE_TZ = ZoneInfo("Europe/Berlin")

# A day shown without a year is a past event; later than this many days from
# today it can only belong to the previous year.
_FUTURE_TOLERANCE_DAYS = 7

_SLOT_STATUS: dict[str, ParcelStatus] = {
    "Start": ParcelStatus.REGISTERED,
    "OnTheRoad": ParcelStatus.IN_TRANSIT,
    "DeliveryDepot": ParcelStatus.IN_TRANSIT,
    "CarLoad": ParcelStatus.OUT_FOR_DELIVERY,
    "Delivered": ParcelStatus.DELIVERED,
}
# ``images/status_<N>.svg`` is a DPD-internal code, not a slot index. Observed
# values only; anything else falls through.
_ICON_STATUS: dict[int, ParcelStatus] = {6: ParcelStatus.DELIVERED}
# (icon, status) pairs already seen together on a real parcel.
_KNOWN_ICON_PAIRS = frozenset(
    {(6, ParcelStatus.DELIVERED), (3, ParcelStatus.RETURNING)}
)

_RETURN_WORD = "retour"
_PICKUP_WORDS = ("paketshop", "pickup", "abhol", "packstation")
_PROBLEM_WORDS = (
    "nicht zugestellt",
    "zustellversuch",
    "verzögert",
    "problem",
    "adresse",
)
_KNOWN_SLOT_TEXTS = frozenset(
    text.casefold()
    for text in (
        "Paketinfo an DPD übergeben",
        "Paket unterwegs",
        "Paket im Paketzustellzentrum",
        "Paket in Zustellung",
        "Paket zugestellt",
        "Retoure Paket an DPD übergeben",
        "Retoure unterwegs",
        "Retoure an Versender",
    )
)
_DAY_MONTH = re.compile(r"^(\d{1,2})\.(\d{1,2})\.$")
_DAY_MONTH_IN_TEXT = re.compile(r"\d{1,2}\.\d{1,2}\.")
_TIME_OF_DAY = re.compile(r"\d{1,2}:\d{2}")
_WEIGHT = re.compile(r"^\d+(?:[.,]\d+)?$")

_WARNED: set[Any] = set()


def _warn_once(key: Any, message: str, *args: Any) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    _LOGGER.warning(message, *args)


def _is_dated(slot: dict[str, str]) -> bool:
    return slot["date"] not in ("", "-")


def _is_reached(slot: dict[str, str]) -> bool:
    return slot["text"] not in ("", "-")


def _is_return(sentence: str, slots: list[dict[str, str]] | None) -> bool:
    """Whether the sentence or any milestone text speaks of a return."""
    return _RETURN_WORD in sentence.casefold() or any(
        _RETURN_WORD in slot["text"].casefold() for slot in slots or []
    )


def _furthest(slots: list[dict[str, str]] | None) -> dict[str, str] | None:
    """Return the last slot, in order, that carries a date."""
    dated = [slot for slot in slots or [] if _is_dated(slot)]
    return dated[-1] if dated else None


def map_parcel_status_de(fields: dict[str, Any]) -> ParcelStatus:
    """Map one parcel to a canonical status. Stops at the first match.

    ``fields`` carries ``status_sentence``, ``status_icon``, ``slots`` (or
    ``None`` when no detail panel was read) and ``history`` (entries with a
    ``status``, newest last, or ``None``).

    The order is the point. A return delivered back to the sender fills a
    milestone and its sentence says "Geliefert", so ``retour`` is checked
    before anything can call it delivered; the icon never overrides a dated
    milestone.
    """
    sentence = (fields.get("status_sentence") or "").casefold()
    slots = fields.get("slots")
    icon = fields.get("status_icon")
    furthest = _furthest(slots)
    furthest_text = furthest["text"].casefold() if furthest else ""

    if _is_return(sentence, slots):
        return ParcelStatus.RETURNING

    if furthest:
        delivered = furthest["slot"] == "Delivered"
    else:
        delivered = (
            icon is not None and _ICON_STATUS.get(icon) is ParcelStatus.DELIVERED
        )
    if not delivered and any(
        word in sentence or word in furthest_text for word in _PICKUP_WORDS
    ):
        return ParcelStatus.AT_PICKUP_POINT

    if furthest:
        return _SLOT_STATUS[furthest["slot"]]

    if any(word in sentence for word in _PROBLEM_WORDS):
        return ParcelStatus.PROBLEM

    if icon in _ICON_STATUS:
        return _ICON_STATUS[icon]

    for entry in reversed(fields.get("history") or []):
        if entry["status"] is not ParcelStatus.UNKNOWN:
            return entry["status"]

    _warn_once(
        ("status", _DAY_MONTH_IN_TEXT.sub("", sentence), icon),
        "DPD Germany parcel could not be classified — help us map it. Open "
        "an issue and paste this line: %s\n  raw_status=%r icon=%r → reported "
        "as 'unknown'",
        _ISSUE_URL,
        fields.get("status_sentence"),
        icon,
    )
    return ParcelStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def _infer_dates(slots: list[dict[str, str]], today: date) -> list[date | None]:
    """Give each milestone day its year, in slot order.

    A day more than a week after today belongs to the previous year, and a
    later milestone never falls before an earlier one.
    """
    inferred: list[date | None] = []
    previous: date | None = None
    for slot in slots:
        raw = slot["date"]
        if raw in ("", "-"):
            inferred.append(None)
            continue
        match = _DAY_MONTH.match(raw)
        if match is None:
            _warn_once(
                ("date", raw),
                "DPD Germany milestone date has an unexpected shape — "
                "report it at %s (%r)",
                _ISSUE_URL,
                raw,
            )
            inferred.append(None)
            continue
        day, month = int(match.group(1)), int(match.group(2))
        year = today.year
        try:
            current = date(year, month, day)
            if current > today + timedelta(days=_FUTURE_TOLERANCE_DAYS):
                year -= 1
                current = date(year, month, day)
            if previous is not None and current < previous:
                current = date(year + 1, month, day)
        except ValueError:
            _LOGGER.debug("DPD Germany milestone day %s does not exist", raw)
            inferred.append(None)
            continue
        previous = current
        inferred.append(current)
    return inferred


def _midnight(day: date | None) -> str | None:
    if day is None:
        return None
    return datetime(day.year, day.month, day.day, tzinfo=_DE_TZ).isoformat()


def _end_of_day(day: date | None) -> str | None:
    if day is None:
        return None
    return datetime(day.year, day.month, day.day, 23, 59, 59, tzinfo=_DE_TZ).isoformat()


def _history(
    slots: list[dict[str, str]] | None, today: date, *, returning: bool
) -> list[dict[str, Any]]:
    """One entry per dated milestone, oldest first.

    On a return every milestone is part of the way back, so none may read as
    a delivery.
    """
    if not slots:
        return []
    entries = []
    for slot, day in zip(slots, _infer_dates(slots, today), strict=True):
        timestamp = _midnight(day)
        if timestamp is None:
            continue
        entries.append(
            {
                "timestamp": timestamp,
                "status": (
                    ParcelStatus.RETURNING if returning else _SLOT_STATUS[slot["slot"]]
                ),
                "raw_status": slot["text"],
            }
        )
    return entries[-HISTORY_MAX_EVENTS:]


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------


def _collect(parcel: dict[str, Any], status: ParcelStatus) -> None:
    """Report, once each, the wordings and codes no parcel has shown yet."""
    detail = parcel.get("detail") or {}
    for slot in detail.get("slots", []):
        text = slot["text"]
        if _is_reached(slot) and text.casefold() not in _KNOWN_SLOT_TEXTS:
            _warn_once(
                ("slot", text.casefold()),
                "DPD Germany milestone text not seen before — help us map "
                "it. Open an issue and paste this line: %s\n  %s=%r",
                _ISSUE_URL,
                slot["slot"],
                text,
            )
    flow_type = detail.get("flow_type", "")
    if flow_type not in ("", "0"):
        _warn_once(
            ("flow", flow_type),
            "DPD Germany parcel flow type %r not seen before — report it at %s",
            flow_type,
            _ISSUE_URL,
        )
    if _TIME_OF_DAY.search(parcel["status_sentence"]):
        _warn_once(
            "time",
            "DPD Germany shows a time of day in a parcel status — report it at %s (%r)",
            _ISSUE_URL,
            parcel["status_sentence"],
        )
    icon = parcel["status_icon"]
    if icon is not None and (icon, status) not in _KNOWN_ICON_PAIRS:
        _warn_once(
            ("icon", icon, status),
            "DPD Germany status icon %s seen with status %s — help us map "
            "it. Open an issue and paste this line: %s",
            icon,
            status.value,
            _ISSUE_URL,
        )


# ---------------------------------------------------------------------------
# Normalizing
# ---------------------------------------------------------------------------


def _weight(text: str) -> float | None:
    if not _WEIGHT.match(text):
        return None
    value = float(text.replace(",", "."))
    return value if value > 0 else None


def normalize_parcel_de(
    parcel: dict[str, Any],
    *,
    include_history: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Return a carrier-agnostic parcel dict for one list row (+ its panel).

    ``sender`` and ``receiver`` stay ``None``: the page names no shipper, and
    the only recipient data sits behind the postcode check, which is never
    submitted. ``today`` anchors the year of the milestone days.
    """
    today = today or datetime.now(_DE_TZ).date()
    detail = parcel["detail"]
    slots = detail["slots"] if detail else None
    stale = parcel.get("stale_detail")
    history_slots = slots if detail else (stale or {}).get("slots")
    history = _history(
        history_slots,
        today,
        returning=_is_return(parcel["status_sentence"], history_slots),
    )

    status = map_parcel_status_de(
        {
            "status_sentence": parcel["status_sentence"],
            "status_icon": parcel["status_icon"],
            "slots": slots,
            "history": history,
        }
    )
    _collect(parcel, status)

    days = dict(zip(SLOTS, _infer_dates(slots, today) if slots else [], strict=False))
    delivered = status is ParcelStatus.DELIVERED
    out_for_delivery = status is ParcelStatus.OUT_FOR_DELIVERY
    barcode = parcel["barcode"]
    weight_text = detail["weight_text"] if detail else ""

    return {
        "carrier": "DPD",
        "barcode": barcode,
        "sender": None,
        "receiver": None,
        "status": status,
        "raw_status": parcel["status_sentence"],
        "delivered": delivered,
        "delivered_at": _midnight(days.get("Delivered")) if delivered else None,
        "planned_from": _midnight(days.get("CarLoad")) if out_for_delivery else None,
        "planned_to": _end_of_day(days.get("CarLoad")) if out_for_delivery else None,
        "pickup": status is ParcelStatus.AT_PICKUP_POINT,
        "pickup_point": None,
        "url": DPD_DE_TRACKING_URL.format(barcode=barcode),
        "weight": _weight(weight_text),
        "dimensions": None,
        "history": (history or None) if include_history else None,
        "raw": {
            "barcode": barcode,
            "label": parcel["label"],
            "status_sentence": parcel["status_sentence"],
            "status_icon": parcel["status_icon"],
            "detail_icon": detail["status_icon"] if detail else None,
            "tab": parcel["tab"],
            "slots": slots,
            "weight_text": weight_text,
            "flow_type": detail["flow_type"] if detail else "",
            "ucid": detail["ucid"] if detail else "",
        },
    }


async def async_get_all_parcels_de(
    de_session: DpdDeSession, *, include_history: bool = False
) -> tuple[list[dict], list[dict]]:
    """Read the account and return ``(incoming, outgoing)`` normalized parcels.

    The Empfang tab is incoming; the Versand and Retoure tabs are outgoing.
    """
    inbox = await de_session.async_get_inbox()
    today = datetime.now(_DE_TZ).date()
    incoming: list[dict] = []
    outgoing: list[dict] = []
    for tab, parcels in inbox.items():
        bucket = incoming if tab == "incoming" else outgoing
        bucket.extend(
            normalize_parcel_de(parcel, include_history=include_history, today=today)
            for parcel in parcels
        )
    return incoming, outgoing


__all__ = [
    "DpdDeSession",
    "async_get_all_parcels_de",
    "map_parcel_status_de",
    "normalize_parcel_de",
]
