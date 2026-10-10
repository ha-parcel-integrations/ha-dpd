"""DPD Austria parcel normalization and status derivation.

The status is *derived* rather than looked up: the parcel-level value is a
monotonic progress stage, and the two facts a stage cannot express — a parcel
waiting at a ParcelShop, and a failed delivery attempt — are read off the
newest history entry instead.

Status text arrives already translated for the account's language, so it is
``raw_status`` material and never a mapping key.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from ...const import HISTORY_MAX_EVENTS, ParcelStatus

_LOGGER = logging.getLogger(__name__)
_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-dpd/issues/new?template=unrecognised_status.yml"

# Austria has DST, so the window/scan timestamps — which carry no offset at
# all — can only be anchored to the carrier's own civil time.
_VIENNA = ZoneInfo("Europe/Vienna")

# No per-parcel tracking link exists: the portal's parcel view is reached from
# the signed-in inbox, not by a URL carrying the parcel number. Germany is in
# the same position and also reports None rather than a generic page, so
# neither declares the ``url`` capability.
_TRACKING_URL = None

# The highest stage the portal ships an icon for. 5 is terminal: the lifecycle
# portal treats every stage but this one as still in progress.
_STAGE_DELIVERED = 5
# The one stage at which the portal itself renders the ``predict`` window, as
# "Heute, HH:MM-HH:MM Uhr". A window on any other stage is not shown by the
# carrier and is not trusted here either.
_STAGE_OUT_FOR_DELIVERY = 4

# Only two ``infoType`` values carry a fact the stage cannot: a pickup point
# and a failed delivery reason.
_INFO_PICKUP_POINT = "shop"
_INFO_FAILURE = "reason"
_KNOWN_INFO_TYPES = frozenset(
    {"shop", "pers", "pers2", "time", "asg", "info", "aviso", "rtg", "reason"}
)
_KNOWN_FAILURE_REASONS = frozenset({"FULL", "TOO_BIG", "UNAVAILABLE"})

# A stable, language-independent identifier per entry. Preferred over the
# numeric stage wherever it maps, because the stage cannot say which of
# several meanings a value carries.
_STATE_NAME_MAP = {
    "ORDER_DATA": ParcelStatus.REGISTERED,        # value 0
    "ON_THE_WAY": ParcelStatus.IN_TRANSIT,        # value 2
    "IN_DEPOT": ParcelStatus.IN_TRANSIT,          # value 3
    "IN_DELIVERY": ParcelStatus.OUT_FOR_DELIVERY,  # value 4
    "DELIVERED": ParcelStatus.DELIVERED,          # value 5
}
_SEEN_STATE_NAMES: set[Any] = set()

# Pre-release reporting. Each set makes its WARNING fire once per distinct
# observation for the lifetime of the process, never per poll.
_SEEN_STAGES: set[Any] = set()
_SEEN_INFO_TYPES: set[Any] = set()
_SEEN_REASONS: set[Any] = set()
_BAD_TIMESTAMPS: set[str] = set()
_UNEXPECTED_SHAPES: set[str] = set()


def _warn_once(bucket: set[Any], key: Any, message: str, *args: Any) -> None:
    """Log ``message`` the first time ``key`` is seen, never its raw value."""
    if key in bucket:
        return
    bucket.add(key)
    _LOGGER.warning(message, *args)


def _warn_unexpected_shape(path: str, value: Any) -> None:
    """Log an unknown payload shape once, never its potentially private value."""
    description = f"{path}: {type(value).__name__}"
    _warn_once(
        _UNEXPECTED_SHAPES,
        description,
        "DPD Austria returned an unexpected payload shape — report it at %s (%s)",
        _ISSUE_URL,
        description,
    )


def _timestamp(value: Any) -> str | None:
    """Parse Austria's ``YYYYMMDDHHMMSS`` scan stamp.

    Fourteen digits, seconds included; the twelve-digit form is accepted too.
    No offset is sent either way, so the stamp can only be read as the
    carrier's own civil time. An unparseable value is reported by *shape*
    only, never by value.
    """
    if not isinstance(value, str) or not value:
        return None
    digits = value.strip()
    if len(digits) in (12, 14) and digits.isdigit():
        try:
            return datetime(
                int(digits[0:4]),
                int(digits[4:6]),
                int(digits[6:8]),
                int(digits[8:10]),
                int(digits[10:12]),
                int(digits[12:14] or 0),
                tzinfo=_VIENNA,
            ).isoformat()
        except ValueError:
            pass
    shape = f"string length={len(digits)} digits={digits.isdigit()}"
    _warn_once(
        _BAD_TIMESTAMPS,
        shape,
        "DPD Austria returned an unparseable timestamp — report its shape at %s (%s)",
        _ISSUE_URL,
        shape,
    )
    return None


def _clock(value: Any) -> str | None:
    """Normalize one ``predict`` bound to ``HH:MM``.

    The portal accepts both spellings the backend sends: already
    colon-separated (``"14:00"``), or a bare ``HHMM`` it chunks itself.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if ":" in raw:
        hours, _, minutes = raw.partition(":")
    elif len(raw) == 4 and raw.isdigit():
        hours, minutes = raw[:2], raw[2:]
    else:
        return None
    if not (hours.isdigit() and minutes.isdigit()):
        return None
    if not (0 <= int(hours) <= 23 and 0 <= int(minutes) <= 59):
        return None
    return f"{int(hours):02d}:{int(minutes):02d}"


def _lifecycle(parcel: dict[str, Any]) -> dict[str, Any]:
    """Return the parcel's lifecycle object, or an empty one."""
    lifecycle = parcel.get("lifecycle")
    if lifecycle is None:
        return {}
    if not isinstance(lifecycle, dict):
        _warn_unexpected_shape("lifecycle", lifecycle)
        return {}
    return lifecycle


def _stage(lifecycle: dict[str, Any]) -> int | None:
    """Read the numeric progress stage out of ``lifecycle.state``.

    ``state`` is an integer stage on an inbox parcel but an object carrying
    ``text``/``delivered`` elsewhere. Both are accepted; only the integer
    form yields a stage.
    """
    state = lifecycle.get("state")
    if isinstance(state, bool):
        _warn_unexpected_shape("lifecycle.state", state)
        return None
    if isinstance(state, int):
        return state
    if isinstance(state, str) and state.strip().isdigit():
        return int(state.strip())
    if isinstance(state, dict):
        return None
    if state is not None:
        _warn_unexpected_shape("lifecycle.state", state)
    return None


def _delivered_flag(lifecycle: dict[str, Any]) -> bool:
    """Read the explicit delivered flag, when the object form carries one."""
    state = lifecycle.get("state")
    return isinstance(state, dict) and state.get("delivered") is True


def _entries(lifecycle: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the lifecycle's scan entries."""
    entries = lifecycle.get("entries")
    if entries is None:
        return []
    if not isinstance(entries, list):
        _warn_unexpected_shape("lifecycle.entries", entries)
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _entry_state(entry: dict[str, Any]) -> dict[str, Any]:
    """Return one entry's ``state`` object, or an empty one."""
    state = entry.get("state")
    if isinstance(state, dict):
        return state
    if state is not None and not isinstance(state, str):
        _warn_unexpected_shape("entries[].state", state)
    return {}


def _entry_status_name(entry: dict[str, Any] | None) -> str | None:
    """Return one entry's stable ``state.name``, reporting unseen values."""
    if entry is None:
        return None
    name = _entry_state(entry).get("name")
    if not isinstance(name, str) or not name:
        return None
    if name not in _STATE_NAME_MAP:
        _warn_once(
            _SEEN_STATE_NAMES,
            name,
            "DPD Austria reported an unmapped status name — report it at %s "
            "(name=%r)",
            _ISSUE_URL,
            name,
        )
        return None
    return name


def _is_return(parcel: dict[str, Any], lifecycle: dict[str, Any]) -> bool:
    """Decide whether DPD is carrying this parcel back.

    The lifecycle's own ``isRetoure``/``isShopRetoure`` booleans are the
    carrier's answer. When either is present they are the whole answer: the
    ``ret`` inbox direction only says which tab the portal files a parcel
    under, so treating it as a return would report ``returning`` for every
    parcel in that tab whatever its real stage — including one still in
    transit or out for delivery.
    """
    flags = [lifecycle.get(key) for key in ("isRetoure", "isShopRetoure")]
    if any(flag is True for flag in flags):
        return True
    if any(isinstance(flag, bool) for flag in flags):
        return False
    return parcel.get("type") == "ret"


def _newest_entry(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the most recent entry, independent of the payload's own order.

    The backend's own ordering is not guaranteed, so the newest is resolved
    by timestamp rather than by position. Entries without a parseable stamp
    cannot win.
    """
    dated = [
        (stamp, entry)
        for entry in entries
        if (stamp := _timestamp(entry.get("datetime"))) is not None
    ]
    if not dated:
        return None
    return max(dated, key=lambda pair: pair[0])[1]


def _info(entry: dict[str, Any] | None) -> tuple[Any, Any]:
    """Return one entry's ``(infoType, infoData)`` pair, reporting new types."""
    if entry is None:
        return None, None
    state = _entry_state(entry)
    info_type = state.get("infoType")
    if isinstance(info_type, str) and info_type not in _KNOWN_INFO_TYPES:
        _warn_once(
            _SEEN_INFO_TYPES,
            info_type,
            "DPD Austria reported an unrecognised infoType — report it at %s "
            "(infoType=%r)",
            _ISSUE_URL,
            info_type,
        )
    return info_type, state.get("infoData")


def _pickup_point_name(info_data: Any) -> str | None:
    """Read a ParcelShop's display name from a ``shop`` entry's ``infoData``."""
    if not isinstance(info_data, dict):
        return None
    name = info_data.get("name1")
    return name if isinstance(name, str) and name else None


def map_parcel_status_at(parcel: dict[str, Any]) -> ParcelStatus:
    """Derive the canonical status for one Austrian parcel.

    Order matters and is deliberate:

    1. A terminal parcel stays terminal. Resolving this first means a
       delivered parcel can never be dragged back to ``PROBLEM`` by a
       first-attempt failure still sitting in its history.
    2. A failed delivery attempt and a return both say more than the stage
       that produced them.
    3. A parcel waiting at a ParcelShop is ``AT_PICKUP_POINT`` whatever its
       stage reads — the case a stage number cannot express.
    4. Only then does the stage speak.
    """
    lifecycle = _lifecycle(parcel)
    if not lifecycle:
        # A parcel the account has not verified carries no lifecycle at all.
        # That is normal, not a gap in the mapping, so it must not ask the
        # user to report anything.
        return ParcelStatus.UNKNOWN

    stage = _stage(lifecycle)
    newest = _newest_entry(_entries(lifecycle))
    info_type, info_data = _info(newest)

    if _delivered_flag(lifecycle) or stage == _STAGE_DELIVERED:
        return ParcelStatus.DELIVERED

    if info_type == _INFO_FAILURE:
        if isinstance(info_data, str) and info_data not in _KNOWN_FAILURE_REASONS:
            _warn_once(
                _SEEN_REASONS,
                info_data,
                "DPD Austria reported an unrecognised delivery-failure reason "
                "— report it at %s (reason=%r)",
                _ISSUE_URL,
                info_data,
            )
        return ParcelStatus.PROBLEM

    if _is_return(parcel, lifecycle):
        return ParcelStatus.RETURNING

    if info_type == _INFO_PICKUP_POINT:
        return ParcelStatus.AT_PICKUP_POINT

    # The newest entry's stable ``state.name`` is more explicit than the bare
    # stage and is preferred where it maps.
    if name := _entry_status_name(newest):
        return _STATE_NAME_MAP[name]

    if stage == _STAGE_OUT_FOR_DELIVERY:
        return ParcelStatus.OUT_FOR_DELIVERY

    if stage in (1, 2, 3):
        # 2 (ON_THE_WAY) and 3 (IN_DEPOT) are confirmed in-transit; 1 has not
        # been seen on a real parcel yet, so it is grouped with them and
        # reported once so the gap closes from pre-release installs.
        if stage == 1:
            _warn_once(
                _SEEN_STAGES,
                stage,
                "DPD Austria stage 1 seen for the first time (reported as "
                "in_transit) — help name it at %s (status=%r)",
                _ISSUE_URL,
                lifecycle.get("state_info"),
            )
        return ParcelStatus.IN_TRANSIT

    if stage == 0:
        return ParcelStatus.REGISTERED

    _warn_once(
        _SEEN_STAGES,
        stage,
        "Unrecognised DPD Austria lifecycle stage — report it at %s (stage=%r)",
        _ISSUE_URL,
        stage,
    )
    return ParcelStatus.UNKNOWN


def build_history_at(
    entries: list[dict[str, Any]], *, max_events: int = HISTORY_MAX_EVENTS
) -> list[dict[str, Any]]:
    """Build the canonical ``history`` from ``lifecycle.entries``.

    ``raw_status`` is the entry's stable identifier rather than the localised
    text beside it, so ``status`` is a real mapped value. An unmapped or
    absent identifier falls to ``ParcelStatus.UNKNOWN`` and is reported once.
    Sorted oldest to newest and capped to ``max_events``.
    """
    dated: list[tuple[str, dict[str, Any]]] = []
    for entry in entries:
        timestamp = _timestamp(entry.get("datetime"))
        if timestamp is None:
            continue
        name = _entry_status_name(entry)
        raw_name = _entry_state(entry).get("name")
        dated.append(
            (
                timestamp,
                {
                    "timestamp": timestamp,
                    "status": (
                        _STATE_NAME_MAP[name] if name else ParcelStatus.UNKNOWN
                    ),
                    "raw_status": raw_name if isinstance(raw_name, str) else None,
                },
            )
        )
    dated.sort(key=lambda pair: pair[0])
    return [item for _, item in dated[-max_events:]]


def normalize_parcel_at(
    parcel: dict[str, Any],
    *,
    include_history: bool = False,
    today: date_cls | None = None,
) -> dict[str, Any]:
    """Normalize one DPD Austria ``parcel/loadList`` record.

    ``today`` anchors the ``predict`` window, which the backend sends as a
    bare time range with no date. It defaults to the current Vienna date and
    is injectable so the mapping stays testable without a frozen clock.
    """
    lifecycle = _lifecycle(parcel)
    stage = _stage(lifecycle)
    entries = _entries(lifecycle)
    newest = _newest_entry(entries)
    info_type, info_data = _info(newest)

    status = map_parcel_status_at(parcel)
    delivered = status is ParcelStatus.DELIVERED

    planned_from, planned_to = _delivery_window(lifecycle, stage, today)
    delivered_at = _delivered_at(lifecycle) if delivered else None

    is_pickup = info_type == _INFO_PICKUP_POINT
    state_info = lifecycle.get("state_info")

    return {
        "carrier": "DPD Austria",
        "barcode": parcel.get("parcelno"),
        # The inbox carries the account holder as ``consignee_name1`` and no
        # sender name at all.
        "sender": None,
        "receiver": parcel.get("consignee_name1"),
        "status": status,
        "raw_status": state_info if isinstance(state_info, str) else None,
        "delivered": delivered,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "delivered_at": delivered_at,
        "pickup": is_pickup,
        "pickup_point": _pickup_point_name(info_data) if is_pickup else None,
        # Austria's inbox carries neither.
        "weight": None,
        "dimensions": None,
        "history": build_history_at(entries) if include_history else None,
        "url": _TRACKING_URL,
        "raw": parcel,
    }


def _delivery_window(
    lifecycle: dict[str, Any], stage: int | None, today: date_cls | None
) -> tuple[str | None, str | None]:
    """Resolve ``planned_from``/``planned_to`` from the ``predict`` window.

    ``predict`` is a ``[from, to]`` time range with no date — the portal
    labels it "Heute" and renders it at the out-for-delivery stage only, so
    a window on any other stage is ignored rather than dated wrongly.
    """
    if stage != _STAGE_OUT_FOR_DELIVERY:
        return None, None
    predict = lifecycle.get("predict")
    if predict is None:
        return None, None
    if not isinstance(predict, list) or len(predict) != 2:
        _warn_unexpected_shape("lifecycle.predict", predict)
        return None, None
    start, end = _clock(predict[0]), _clock(predict[1])
    if start is None or end is None:
        _warn_unexpected_shape("lifecycle.predict[]", predict[0])
        return None, None
    day = today or datetime.now(_VIENNA).date()
    return (
        datetime.fromisoformat(f"{day.isoformat()}T{start}").replace(
            tzinfo=_VIENNA
        ).isoformat(),
        datetime.fromisoformat(f"{day.isoformat()}T{end}").replace(
            tzinfo=_VIENNA
        ).isoformat(),
    )


def _delivered_at(lifecycle: dict[str, Any]) -> str | None:
    """Resolve the delivery timestamp.

    Prefers the newest scan entry over ``deliver_date``: Germany's
    equivalent top-level field turned out to be unusable for this, so the
    scan — which is dated by the carrier's own event — wins where both are
    present.
    """
    newest = _newest_entry(_entries(lifecycle))
    if newest is not None and (stamp := _timestamp(newest.get("datetime"))):
        return stamp
    deliver_date = lifecycle.get("deliver_date")
    if deliver_date is None:
        return None
    if not isinstance(deliver_date, dict):
        _warn_unexpected_shape("lifecycle.deliver_date", deliver_date)
        return None
    date_part, time_part = deliver_date.get("date"), deliver_date.get("time")
    if not isinstance(date_part, str) or not date_part:
        return None
    clock = _clock(time_part) or "00:00"
    for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            day = datetime.strptime(date_part.strip(), pattern).date()
        except ValueError:
            continue
        return datetime.fromisoformat(f"{day.isoformat()}T{clock}").replace(
            tzinfo=_VIENNA
        ).isoformat()
    _warn_unexpected_shape("lifecycle.deliver_date.date", date_part)
    return None
