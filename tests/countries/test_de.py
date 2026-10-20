"""Tests for DPD Germany's status map and normalizer (pure, no HTTP)."""
import logging
from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dpd.const import ParcelStatus
from custom_components.dpd.countries.de import (
    async_get_all_parcels_de,
    map_parcel_status_de,
    normalize_parcel_de,
)

from ..payloads import DE_BARCODE, DE_BARCODE_2

TODAY = date(2026, 10, 20)
_SLOT_NAMES = ("Start", "OnTheRoad", "DeliveryDepot", "CarLoad", "Delivered")
_START = "Paketinfo an DPD übergeben"
_ROAD = "Paket unterwegs"
_DEPOT = "Paket im Paketzustellzentrum"
_CAR = "Paket in Zustellung"
_DONE = "Paket zugestellt"


def _slots(*pairs):
    pairs = list(pairs) + [("-", "")] * (5 - len(pairs))
    return [
        {"slot": name, "text": text, "date": day}
        for name, (text, day) in zip(_SLOT_NAMES, pairs)
    ]


def _parcel(
    sentence="Paket unterwegs",
    icon=1,
    slots=None,
    *,
    tab="incoming",
    weight="",
    flow="0",
    stale=None,
):
    detail = (
        {
            "identity": DE_BARCODE,
            "slots": slots,
            "status_icon": icon,
            "weight_text": weight,
            "flow_type": flow,
            "ucid": "",
        }
        if slots is not None
        else None
    )
    return {
        "barcode": DE_BARCODE,
        "label": "DPD Paket",
        "status_sentence": sentence,
        "status_icon": icon,
        "tab": tab,
        "detail": detail,
        "stale_detail": stale,
    }


def _status(parcel):
    return normalize_parcel_de(parcel, today=TODAY)["status"]


# -- status map: one test per rung -------------------------------------------


def test_rung1_a_return_beats_delivered():
    """A return delivered back to the sender fills the Delivered slot."""
    slots = _slots(
        ("Retoure Paket an DPD übergeben", "05.10."),
        ("Retoure unterwegs", "07.10."),
        ("Retoure an Versender", "09.10."),
        ("-", ""),
        (_DONE, "09.10."),
    )
    parcel = _parcel("Retoure an Versender - Geliefert am: 09.10.", 3, slots, tab="returning")
    result = normalize_parcel_de(parcel, today=TODAY)
    assert result["status"] is ParcelStatus.RETURNING
    assert result["delivered"] is False
    assert result["delivered_at"] is None


def test_rung1_retour_in_the_sentence_alone():
    assert _status(_parcel("Retoure unterwegs")) is ParcelStatus.RETURNING


def test_rung2_pickup_words_beat_the_furthest_slot():
    slots = _slots((_START, "18.10."), (_ROAD, "19.10."), (_DEPOT, "20.10."))
    parcel = _parcel("Paket liegt im Paketshop zur Abholung bereit", 5, slots)
    assert _status(parcel) is ParcelStatus.AT_PICKUP_POINT
    assert normalize_parcel_de(parcel, today=TODAY)["pickup"] is True


def test_rung2_pickup_in_the_furthest_slot_text():
    slots = _slots((_START, "18.10."), ("Paket in Packstation", "19.10."))
    assert _status(_parcel("Unterwegs", 1, slots)) is ParcelStatus.AT_PICKUP_POINT


def test_rung2_does_not_apply_once_delivered():
    slots = _slots((_START, "18.10."), (_ROAD, "19.10."), (_DEPOT, "19.10."), (_CAR, "20.10."), (_DONE, "20.10."))
    parcel = _parcel("Geliefert, Abholung im Paketshop", 6, slots)
    assert _status(parcel) is ParcelStatus.DELIVERED


def test_rung2_delivered_icon_without_slots_is_not_a_pickup():
    assert _status(_parcel("Abholung im Paketshop", 6)) is ParcelStatus.DELIVERED


@pytest.mark.parametrize(
    ("dated", "expected"),
    [
        (1, ParcelStatus.REGISTERED),
        (2, ParcelStatus.IN_TRANSIT),
        (3, ParcelStatus.IN_TRANSIT),
        (4, ParcelStatus.OUT_FOR_DELIVERY),
        (5, ParcelStatus.DELIVERED),
    ],
)
def test_rung3_the_furthest_dated_slot_decides(dated, expected):
    texts = (_START, _ROAD, _DEPOT, _CAR, _DONE)[:dated]
    slots = _slots(*[(text, "18.10.") for text in texts])
    assert _status(_parcel("Unterwegs", 1, slots)) is expected


def test_rung3_a_dated_slot_beats_the_icon():
    slots = _slots((_START, "18.10."), (_ROAD, "19.10."))
    assert _status(_parcel("Unterwegs", 6, slots)) is ParcelStatus.IN_TRANSIT


def test_rung3_a_slot_with_text_but_no_date_is_not_reached():
    slots = _slots((_START, "18.10."), (_ROAD, ""))
    assert _status(_parcel("Unterwegs", 1, slots)) is ParcelStatus.REGISTERED


def test_rung4_problem_words_in_the_sentence():
    assert _status(_parcel("Zustellversuch nicht erfolgreich", 2)) is ParcelStatus.PROBLEM


def test_rung4_loses_to_a_dated_slot():
    slots = _slots((_START, "18.10."), (_ROAD, "19.10."), (_DEPOT, "19.10."), (_CAR, "20.10."))
    assert _status(_parcel("Zustellversuch", 2, slots)) is ParcelStatus.OUT_FOR_DELIVERY


def test_rung5_icon_six_without_slots_is_delivered():
    assert _status(_parcel("Zugestellt", 6)) is ParcelStatus.DELIVERED


def test_rung6_history_fallback_uses_the_newest_mappable_entry():
    fields = {
        "status_sentence": "???",
        "status_icon": 9,
        "slots": None,
        "history": [
            {"status": ParcelStatus.REGISTERED},
            {"status": ParcelStatus.IN_TRANSIT},
            {"status": ParcelStatus.UNKNOWN},
        ],
    }
    assert map_parcel_status_de(fields) is ParcelStatus.IN_TRANSIT


def test_rung6_stale_detail_feeds_the_history_fallback():
    stale = {"slots": _slots((_START, "18.10."), (_ROAD, "19.10."))}
    parcel = _parcel("Neue Formulierung", 9, stale=stale)
    assert _status(parcel) is ParcelStatus.IN_TRANSIT


def test_rung7_unknown_warns_once_with_the_issue_link(caplog):
    parcel = _parcel("Etwas ganz Neues", 9)
    with caplog.at_level(logging.WARNING):
        assert _status(parcel) is ParcelStatus.UNKNOWN
        assert _status(parcel) is ParcelStatus.UNKNOWN
    warnings = [r.getMessage() for r in caplog.records if "classified" in r.getMessage()]
    assert len(warnings) == 1
    assert "unrecognised_status.yml" in warnings[0]
    assert "Etwas ganz Neues" in warnings[0]
    assert "icon=9" in warnings[0]


def test_the_unknown_warning_ignores_the_dates_in_the_sentence(caplog):
    with caplog.at_level(logging.WARNING):
        _status(_parcel("Etwas Neues am: 09.10.", 9))
        _status(_parcel("Etwas Neues am: 10.10.", 9))
    assert len([r for r in caplog.records if "classified" in r.getMessage()]) == 1


def test_empty_fields_are_unknown():
    assert map_parcel_status_de({}) is ParcelStatus.UNKNOWN


# -- year inference ------------------------------------------------------------


def _history_days(slots, today):
    parcel = _parcel("Unterwegs", 1, slots)
    result = normalize_parcel_de(parcel, include_history=True, today=today)
    return [entry["timestamp"][:10] for entry in result["history"] or []]


def test_year_plain():
    slots = _slots((_START, "15.10."), (_ROAD, "18.10."))
    assert _history_days(slots, TODAY) == ["2026-10-15", "2026-10-18"]


def test_year_new_year_rollover():
    slots = _slots((_START, "29.12."), (_ROAD, "02.01."))
    assert _history_days(slots, date(2027, 1, 3)) == ["2026-12-29", "2027-01-02"]


def test_year_a_day_within_the_week_ahead_stays_this_year():
    slots = _slots((_START, "25.10."))
    assert _history_days(slots, TODAY) == ["2026-10-25"]


def test_year_a_day_beyond_the_week_ahead_is_last_year():
    slots = _slots((_START, "28.10."))
    assert _history_days(slots, TODAY) == ["2025-10-28"]


def test_year_monotonic_correction_moves_a_later_slot_forward():
    slots = _slots((_START, "28.10."), (_ROAD, "01.10."))
    assert _history_days(slots, TODAY) == ["2025-10-28", "2026-10-01"]


def test_year_feb_29_in_a_non_leap_year_is_none_and_skipped():
    slots = _slots((_START, "29.02."), (_ROAD, "01.03."))
    assert _history_days(slots, date(2027, 3, 5)) == ["2027-03-01"]


def test_year_feb_29_in_a_leap_year_is_kept():
    slots = _slots((_START, "29.02."))
    assert _history_days(slots, date(2028, 3, 5)) == ["2028-02-29"]


def test_year_previous_year_feb_29_that_does_not_exist_is_none():
    slots = _slots((_START, "29.02."))
    assert _history_days(slots, date(2028, 2, 1)) == []


def test_year_monotonic_forward_move_onto_a_missing_leap_day_is_none():
    slots = _slots((_START, "01.03."), (_ROAD, "29.02."))
    assert _history_days(slots, date(2028, 3, 5)) == ["2028-03-01"]


def test_a_date_of_an_unexpected_shape_is_skipped_and_warns_once(caplog):
    slots = _slots((_START, "2026-10-15"), (_ROAD, "18.10."))
    with caplog.at_level(logging.WARNING):
        assert _history_days(slots, TODAY) == ["2026-10-18"]
        _history_days(slots, TODAY)
    shapes = [r for r in caplog.records if "unexpected shape" in r.getMessage()]
    assert len(shapes) == 1


def test_a_day_that_does_not_exist_is_skipped():
    assert _history_days(_slots((_START, "31.02.")), TODAY) == []


# -- normalized parcel ---------------------------------------------------------


def test_weight_comma_decimal_is_kilograms():
    slots = _slots((_START, "18.10."))
    assert normalize_parcel_de(_parcel("U", 1, slots, weight="10,95"), today=TODAY)["weight"] == 10.95


@pytest.mark.parametrize("text", ["", "n/a", "0", "0,00", "1,2,3"])
def test_weight_empty_or_unusable_is_none(text):
    slots = _slots((_START, "18.10."))
    assert normalize_parcel_de(_parcel("U", 1, slots, weight=text), today=TODAY)["weight"] is None


def test_weight_without_a_detail_panel_is_none():
    assert normalize_parcel_de(_parcel("Unterwegs", 1), today=TODAY)["weight"] is None


def test_planned_window_only_when_out_for_delivery():
    slots = _slots((_START, "18.10."), (_ROAD, "19.10."), (_DEPOT, "19.10."), (_CAR, "20.10."))
    result = normalize_parcel_de(_parcel("U", 4, slots), today=TODAY)
    assert result["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert result["planned_from"] == "2026-10-20T00:00:00+02:00"
    assert result["planned_to"] == "2026-10-20T23:59:59+02:00"
    assert result["delivered_at"] is None

    transit = normalize_parcel_de(_parcel("U", 4, _slots((_START, "18.10."))), today=TODAY)
    assert transit["planned_from"] is None
    assert transit["planned_to"] is None


def test_out_for_delivery_with_an_unreadable_day_has_no_window():
    slots = _slots((_START, "18.10."), (_ROAD, "19.10."), (_DEPOT, "19.10."), (_CAR, "heute"))
    result = normalize_parcel_de(_parcel("U", 4, slots), today=TODAY)
    assert result["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert result["planned_from"] is None
    assert result["planned_to"] is None


def test_delivered_at_only_when_delivered():
    slots = _slots((_START, "15.10."), (_ROAD, "16.10."), (_DEPOT, "17.10."), (_CAR, "18.10."), (_DONE, "18.10."))
    result = normalize_parcel_de(_parcel("Geliefert", 6, slots), today=TODAY)
    assert result["status"] is ParcelStatus.DELIVERED
    assert result["delivered"] is True
    assert result["delivered_at"] == "2026-10-18T00:00:00+02:00"
    assert result["planned_from"] is None


def test_delivered_by_icon_has_no_delivery_day():
    result = normalize_parcel_de(_parcel("Geliefert", 6), today=TODAY)
    assert result["delivered"] is True
    assert result["delivered_at"] is None


def test_the_contract_fields():
    slots = _slots((_START, "18.10."))
    result = normalize_parcel_de(_parcel("Unterwegs", 1, slots, weight="1,5"), today=TODAY)
    assert result["carrier"] == "DPD"
    assert result["barcode"] == DE_BARCODE
    assert result["sender"] is None
    assert result["receiver"] is None
    assert result["raw_status"] == "Unterwegs"
    assert result["pickup"] is False
    assert result["pickup_point"] is None
    assert result["dimensions"] is None
    assert result["url"] == f"https://my.dpd.de/redirect.aspx?action=12&parcelno={DE_BARCODE}"
    assert set(result) == {
        "carrier", "barcode", "sender", "receiver", "status", "raw_status",
        "delivered", "delivered_at", "planned_from", "planned_to", "pickup",
        "pickup_point", "url", "weight", "dimensions", "history", "raw",
    }


def test_today_defaults_to_the_berlin_date():
    result = normalize_parcel_de(_parcel("U", 1, _slots((_START, "01.01."))))
    assert result["history"] is None


# -- history -------------------------------------------------------------------


def test_history_is_oldest_first_and_skips_unreached_slots():
    slots = _slots((_START, "15.10."), (_ROAD, "16.10."), ("-", ""), (_CAR, "18.10."))
    result = normalize_parcel_de(_parcel("U", 4, slots), include_history=True, today=TODAY)
    assert result["history"] == [
        {"timestamp": "2026-10-15T00:00:00+02:00", "status": ParcelStatus.REGISTERED, "raw_status": _START},
        {"timestamp": "2026-10-16T00:00:00+02:00", "status": ParcelStatus.IN_TRANSIT, "raw_status": _ROAD},
        {"timestamp": "2026-10-18T00:00:00+02:00", "status": ParcelStatus.OUT_FOR_DELIVERY, "raw_status": _CAR},
    ]


def test_history_is_gated_by_the_option():
    slots = _slots((_START, "15.10."))
    assert normalize_parcel_de(_parcel("U", 1, slots), today=TODAY)["history"] is None
    assert normalize_parcel_de(_parcel("U", 1, slots), include_history=True, today=TODAY)["history"]


def test_history_with_no_dated_slot_is_none():
    result = normalize_parcel_de(_parcel("U", 1, _slots()), include_history=True, today=TODAY)
    assert result["history"] is None


def test_history_of_a_return_is_not_marked_delivered():
    slots = _slots(("Retoure Paket an DPD übergeben", "05.10."), ("Retoure unterwegs", "07.10."), ("Retoure an Versender", "09.10."), ("-", ""), (_DONE, "09.10."))
    result = normalize_parcel_de(_parcel("Retoure an Versender", 3, slots), include_history=True, today=TODAY)
    assert {entry["status"] for entry in result["history"]} == {ParcelStatus.RETURNING}


def test_history_without_a_fresh_detail_comes_from_the_stale_one():
    stale = {"slots": _slots((_START, "15.10."))}
    result = normalize_parcel_de(_parcel("Neu", 9, stale=stale), include_history=True, today=TODAY)
    assert [e["raw_status"] for e in result["history"]] == [_START]
    assert result["raw"]["slots"] is None


# -- raw -----------------------------------------------------------------------


def test_raw_carries_every_extracted_field_and_no_postcode():
    slots = _slots((_START, "15.10."))
    result = normalize_parcel_de(_parcel("U", 2, slots, weight="2,5", tab="sending"), today=TODAY)
    assert result["raw"] == {
        "barcode": DE_BARCODE,
        "label": "DPD Paket",
        "status_sentence": "U",
        "status_icon": 2,
        "detail_icon": 2,
        "tab": "sending",
        "slots": slots,
        "weight_text": "2,5",
        "flow_type": "0",
        "ucid": "",
    }
    assert not any("zip" in key.lower() or "post" in key.lower() for key in result["raw"])


def test_raw_without_a_detail_panel():
    raw = normalize_parcel_de(_parcel("U", 2), today=TODAY)["raw"]
    assert raw["slots"] is None
    assert raw["detail_icon"] is None
    assert raw["weight_text"] == ""


# -- collectors ----------------------------------------------------------------


def _warnings(caplog):
    return [r.getMessage() for r in caplog.records]


def test_unknown_slot_text_warns_once_per_text(caplog):
    slots = _slots(("Ein neuer Text", "15.10."), (_ROAD, "16.10."))
    with caplog.at_level(logging.WARNING):
        normalize_parcel_de(_parcel("U", 1, slots), today=TODAY)
        normalize_parcel_de(_parcel("U", 1, slots), today=TODAY)
    hits = [m for m in _warnings(caplog) if "milestone text" in m]
    assert len(hits) == 1
    assert "Ein neuer Text" in hits[0]


def test_known_slot_texts_do_not_warn(caplog):
    slots = _slots((_START, "15.10."), (_ROAD, "16.10."))
    with caplog.at_level(logging.WARNING):
        normalize_parcel_de(_parcel("U", 2, slots), today=TODAY)
    assert not [m for m in _warnings(caplog) if "milestone text" in m]


def test_a_new_flow_type_warns_once(caplog):
    slots = _slots((_START, "15.10."))
    with caplog.at_level(logging.WARNING):
        normalize_parcel_de(_parcel("U", 2, slots, flow="2"), today=TODAY)
        normalize_parcel_de(_parcel("U", 2, slots, flow="2"), today=TODAY)
        normalize_parcel_de(_parcel("U", 2, slots, flow="0"), today=TODAY)
    assert len([m for m in _warnings(caplog) if "flow type" in m]) == 1


def test_a_time_of_day_in_the_sentence_warns_once(caplog):
    with caplog.at_level(logging.WARNING):
        normalize_parcel_de(_parcel("Zustellung heute 14:00 - 16:00", 4), today=TODAY)
        normalize_parcel_de(_parcel("Zustellung heute 14:00 - 16:00", 4), today=TODAY)
    assert len([m for m in _warnings(caplog) if "time of day" in m]) == 1


def test_an_unseen_icon_status_pair_warns_once_and_a_seen_one_does_not(caplog):
    slots = _slots((_START, "15.10."), (_ROAD, "16.10."))
    with caplog.at_level(logging.WARNING):
        normalize_parcel_de(_parcel("U", 2, slots), today=TODAY)
        normalize_parcel_de(_parcel("U", 2, slots), today=TODAY)
        normalize_parcel_de(_parcel("Geliefert", 6), today=TODAY)
    icons = [m for m in _warnings(caplog) if "status icon" in m]
    assert len(icons) == 1
    assert "icon 2" in icons[0]


# -- fetching ------------------------------------------------------------------


async def test_incoming_tab_is_incoming_and_the_others_outgoing():
    def row(tab, barcode):
        return {**_parcel("U", 1, tab=tab), "barcode": barcode}

    session = MagicMock()
    session.async_get_inbox = AsyncMock(
        return_value={
            "incoming": [row("incoming", DE_BARCODE)],
            "sending": [row("sending", DE_BARCODE_2)],
            "returning": [row("returning", "01234567890125")],
        }
    )
    incoming, outgoing = await async_get_all_parcels_de(session, include_history=True)
    assert [p["barcode"] for p in incoming] == [DE_BARCODE]
    assert [p["barcode"] for p in outgoing] == [DE_BARCODE_2, "01234567890125"]
