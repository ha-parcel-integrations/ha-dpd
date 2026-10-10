"""Tests for DPD Austria canonical normalization and status derivation."""
from datetime import date

import pytest

from custom_components.dpd.const import ParcelStatus
from custom_components.dpd.countries.at import (
    _clock,
    _timestamp,
    build_history_at,
    map_parcel_status_at,
    normalize_parcel_at,
)

# 2026-10-09 14:05:00 — outside Vienna's DST switch dates either way. Fourteen
# digits, which is what a real parcel carries.
_STAMP = "20261009140500"
_STAMP_ISO = "2026-10-09T14:05:00+02:00"


def _entry(
    stamp: str = _STAMP,
    *,
    name="DELIVERED",
    text="Zugestellt",
    info_type=None,
    info_data=None,
):
    state = {"text": text}
    if name is not None:
        state["name"] = name
    if info_type is not None:
        state["infoType"] = info_type
        state["infoData"] = info_data
    return {"datetime": stamp, "state": state}


def _parcel(stage=2, *, direction="inc", entries=None, **lifecycle):
    return {
        "id": 4711,
        "parcelno": "01234567890123",
        "parcel_name": "Headphones",
        "type": direction,
        "verified": True,
        "consignee_name1": "",
        "plz": "",
        "city": "",
        "gkz": "",
        "order_status": 1,
        "date_created": "2026-10-08 09:00:00",
        "lifecycle": {
            "state": stage,
            "state_info": "Paket ist unterwegs",
            "entries": entries if entries is not None else [_entry(name=None)],
            "predict": None,
            "deliver_date": None,
            "lang": "de",
            **lifecycle,
        },
    }


# --------------------------------------------------------------------------
# Status derivation — every stage, and each precedence row
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        (0, ParcelStatus.REGISTERED),
        (1, ParcelStatus.IN_TRANSIT),
        (2, ParcelStatus.IN_TRANSIT),
        (3, ParcelStatus.IN_TRANSIT),
        (4, ParcelStatus.OUT_FOR_DELIVERY),
        (5, ParcelStatus.DELIVERED),
    ],
)
def test_every_stage_the_portal_ships_an_icon_for_maps_to_a_status(stage, expected):
    assert map_parcel_status_at(_parcel(stage)) is expected


def test_a_stage_outside_the_icon_range_is_unknown_and_warns(caplog):
    assert map_parcel_status_at(_parcel(9)) is ParcelStatus.UNKNOWN
    assert "Unrecognised DPD Austria lifecycle stage" in caplog.text


def test_a_missing_lifecycle_is_unknown_not_an_error():
    assert map_parcel_status_at({"parcelno": "1"}) is ParcelStatus.UNKNOWN


def test_delivered_wins_over_a_failure_still_sitting_in_history():
    """A first-attempt failure must not drag a delivered parcel to problem."""
    parcel = _parcel(
        5,
        entries=[
            _entry("202610081000", info_type="reason", info_data="FULL"),
            _entry("202610091405", text="Zugestellt"),
        ],
    )
    assert map_parcel_status_at(parcel) is ParcelStatus.DELIVERED


def test_a_failed_parcel_station_attempt_is_a_problem():
    parcel = _parcel(4, entries=[_entry(info_type="reason", info_data="TOO_BIG")])
    assert map_parcel_status_at(parcel) is ParcelStatus.PROBLEM


def test_an_unrecognised_failure_reason_still_maps_but_warns(caplog):
    parcel = _parcel(4, entries=[_entry(info_type="reason", info_data="FLOODED")])
    assert map_parcel_status_at(parcel) is ParcelStatus.PROBLEM
    assert "unrecognised delivery-failure reason" in caplog.text


def test_a_failure_outranks_the_returns_direction():
    parcel = _parcel(
        3, direction="ret", entries=[_entry(info_type="reason", info_data="FULL")]
    )
    assert map_parcel_status_at(parcel) is ParcelStatus.PROBLEM


def test_the_returns_direction_is_returning():
    assert map_parcel_status_at(_parcel(3, direction="ret")) is ParcelStatus.RETURNING


def test_a_pickup_point_outranks_out_for_delivery():
    """The case a stage number cannot express."""
    parcel = _parcel(
        4, entries=[_entry(info_type="shop", info_data={"bid": 7, "name1": "Shop"})]
    )
    assert map_parcel_status_at(parcel) is ParcelStatus.AT_PICKUP_POINT


def test_the_object_form_of_state_is_read_for_its_delivered_flag():
    """The tracking widget's payload uses an object where the inbox uses an int."""
    parcel = _parcel(2)
    parcel["lifecycle"]["state"] = {"text": "Zugestellt", "delivered": True}
    assert map_parcel_status_at(parcel) is ParcelStatus.DELIVERED


def test_an_unrecognised_info_type_warns_without_changing_the_status(caplog):
    parcel = _parcel(
        2, entries=[_entry(name=None, info_type="teleport", info_data="x")]
    )
    assert map_parcel_status_at(parcel) is ParcelStatus.IN_TRANSIT
    assert "unrecognised infoType" in caplog.text


def test_the_one_unseen_stage_reports_itself_for_refinement(caplog):
    """Stages 2 and 3 are named by a real parcel; 1 never has been."""
    map_parcel_status_at(_parcel(1, entries=[_entry(name=None)]))
    assert "stage 1 seen for the first time" in caplog.text


def test_a_confirmed_stage_does_not_ask_to_be_named(caplog):
    map_parcel_status_at(_parcel(3, entries=[_entry(name="IN_DEPOT")]))
    assert "seen for the first time" not in caplog.text


# --------------------------------------------------------------------------
# Timestamps and the predict window
# --------------------------------------------------------------------------


def test_the_fourteen_digit_stamp_a_real_parcel_sends_is_read_as_vienna_time():
    """Regression: 14 digits with seconds, confirmed live 2026-10-10.

    The renderer only slices positions 0-11 because it never shows seconds,
    which made a static read suggest 12 — and silently dropped every
    timestamp, including ``delivered_at``.
    """
    assert _timestamp("20260806102900") == "2026-08-06T10:29:00+02:00"


def test_the_twelve_digit_stamp_the_renderer_works_with_is_still_accepted():
    assert _timestamp("202610091405") == _STAMP_ISO


def test_a_winter_stamp_gets_the_winter_offset():
    assert _timestamp("20260115103000") == "2026-01-15T10:30:00+01:00"


@pytest.mark.parametrize("value", ["", None, "2026-10-09", "20261009140", "notadate!!!"])
def test_an_unparseable_stamp_is_dropped(value):
    assert _timestamp(value) is None


def test_an_unparseable_stamp_logs_its_shape_and_never_its_value(caplog):
    assert _timestamp("9999999999999X") is None
    assert "unparseable timestamp" in caplog.text
    assert "9999999999999X" not in caplog.text


@pytest.mark.parametrize(
    ("value", "expected"),
    [("14:00", "14:00"), ("1400", "14:00"), ("9:05", "09:05"), ("0905", "09:05")],
)
def test_both_predict_spellings_normalize(value, expected):
    assert _clock(value) == expected


@pytest.mark.parametrize("value", ["", "25:00", "14:99", "abc", "140", None, 1400])
def test_a_bad_clock_value_is_dropped(value):
    assert _clock(value) is None


def test_the_predict_window_is_dated_to_today_at_the_delivery_stage():
    parcel = _parcel(4, predict=["0900", "1300"])
    result = normalize_parcel_at(parcel, today=date(2026, 10, 9))
    assert result["planned_from"] == "2026-10-09T09:00:00+02:00"
    assert result["planned_to"] == "2026-10-09T13:00:00+02:00"


def test_the_predict_window_is_ignored_on_any_other_stage():
    """The portal only ever renders it as "Heute" at stage 4."""
    result = normalize_parcel_at(_parcel(2, predict=["0900", "1300"]))
    assert result["planned_from"] is None
    assert result["planned_to"] is None


def test_a_malformed_predict_window_is_dropped_and_warns(caplog):
    result = normalize_parcel_at(_parcel(4, predict=["0900"]))
    assert result["planned_from"] is None
    assert "unexpected payload shape" in caplog.text


# --------------------------------------------------------------------------
# normalize_parcel_at
# --------------------------------------------------------------------------


def test_normalize_maps_canonical_fields_and_keeps_the_full_raw_record():
    raw = _parcel(2)
    result = normalize_parcel_at(raw)
    assert result["carrier"] == "DPD Austria"
    assert result["barcode"] == "01234567890123"
    assert result["status"] is ParcelStatus.IN_TRANSIT
    assert result["raw_status"] == "Paket ist unterwegs"
    assert result["delivered"] is False
    assert result["pickup"] is False
    assert result["pickup_point"] is None
    assert result["weight"] is None
    assert result["dimensions"] is None
    assert result["history"] is None
    # No per-parcel tracking page exists, so url is None — the same as DE,
    # and why Austria does not declare the ``url`` capability.
    assert result["url"] is None
    assert result["raw"] == raw


def test_a_delivered_parcel_takes_its_timestamp_from_the_newest_scan():
    result = normalize_parcel_at(_parcel(5))
    assert result["delivered"] is True
    assert result["delivered_at"] == _STAMP_ISO


def test_deliver_date_is_the_fallback_when_no_scan_is_dated():
    parcel = _parcel(5, entries=[], deliver_date={"date": "09.10.2026", "time": "1405"})
    assert normalize_parcel_at(parcel)["delivered_at"] == _STAMP_ISO


def test_deliver_date_also_accepts_an_iso_date():
    parcel = _parcel(5, entries=[], deliver_date={"date": "2026-10-09", "time": "14:05"})
    assert normalize_parcel_at(parcel)["delivered_at"] == _STAMP_ISO


def test_a_deliver_date_without_a_time_falls_back_to_midnight():
    parcel = _parcel(5, entries=[], deliver_date={"date": "09.10.2026", "time": None})
    assert normalize_parcel_at(parcel)["delivered_at"] == "2026-10-09T00:00:00+02:00"


def test_an_undated_parcel_has_no_delivered_timestamp():
    assert normalize_parcel_at(_parcel(5, entries=[]))["delivered_at"] is None


def test_a_non_delivered_parcel_never_reports_a_delivered_timestamp():
    parcel = _parcel(2, deliver_date={"date": "09.10.2026", "time": "1405"})
    assert normalize_parcel_at(parcel)["delivered_at"] is None


def test_a_pickup_point_name_is_surfaced():
    parcel = _parcel(
        3,
        entries=[
            _entry(info_type="shop", info_data={"bid": 7, "name1": "Trafik Hauptplatz"})
        ],
    )
    result = normalize_parcel_at(parcel)
    assert result["status"] is ParcelStatus.AT_PICKUP_POINT
    assert result["pickup"] is True
    assert result["pickup_point"] == "Trafik Hauptplatz"


def test_a_pickup_entry_without_a_name_still_flags_the_pickup():
    parcel = _parcel(3, entries=[_entry(info_type="shop", info_data={"bid": 7})])
    result = normalize_parcel_at(parcel)
    assert result["pickup"] is True
    assert result["pickup_point"] is None


def test_an_unverified_parcel_with_no_lifecycle_normalizes_without_raising():
    parcel = {"parcelno": "1", "type": "inc", "verified": False}
    result = normalize_parcel_at(parcel, include_history=True)
    assert result["status"] is ParcelStatus.UNKNOWN
    assert result["history"] == []
    assert result["delivered"] is False


def test_a_lifecycle_of_the_wrong_shape_warns_and_degrades(caplog):
    result = normalize_parcel_at({"parcelno": "1", "lifecycle": "nope"})
    assert result["status"] is ParcelStatus.UNKNOWN
    assert "unexpected payload shape" in caplog.text


# --------------------------------------------------------------------------
# History
# --------------------------------------------------------------------------


def test_history_is_built_only_when_requested():
    assert normalize_parcel_at(_parcel(2))["history"] is None
    parcel = _parcel(5, entries=[_entry(name="DELIVERED")])
    history = normalize_parcel_at(parcel, include_history=True)["history"]
    assert history == [
        {
            "timestamp": _STAMP_ISO,
            "status": ParcelStatus.DELIVERED,
            "raw_status": "DELIVERED",
        }
    ]


def test_history_is_sorted_oldest_to_newest_whatever_order_it_arrives_in():
    entries = [
        _entry("20261009140500", name="DELIVERED"),
        _entry("20261007100000", name="ORDER_DATA"),
        _entry("20261008120000", name="ON_THE_WAY"),
    ]
    history = build_history_at(entries)
    assert [item["raw_status"] for item in history] == [
        "ORDER_DATA",
        "ON_THE_WAY",
        "DELIVERED",
    ]
    assert [item["status"] for item in history] == [
        ParcelStatus.REGISTERED,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.DELIVERED,
    ]


def test_history_drops_undated_entries():
    assert build_history_at([_entry("bogus")]) == []


def test_history_is_capped_to_the_most_recent_events():
    entries = [
        _entry(f"2026100910{minute:02d}00", name="ON_THE_WAY")
        for minute in range(30)
    ]
    history = build_history_at(entries, max_events=5)
    assert [item["timestamp"][11:16] for item in history] == [
        "10:25",
        "10:26",
        "10:27",
        "10:28",
        "10:29",
    ]


def test_a_history_entry_without_a_name_never_guesses_from_its_prose():
    """The localised ``text`` is never a mapping key."""
    history = build_history_at([_entry(name=None, text="Zugestellt")])
    assert history[0]["status"] is ParcelStatus.UNKNOWN
    assert history[0]["raw_status"] is None


# --------------------------------------------------------------------------
# Defensive branches — every one of these is a payload shape the portal's
# own renderer does not produce, so they exist to degrade rather than raise.
# --------------------------------------------------------------------------


def test_a_stamp_with_impossible_components_is_dropped(caplog):
    """Right length, all digits, but month 99 — ValueError, not a crash."""
    assert _timestamp("20999999140500") is None
    assert "unparseable timestamp" in caplog.text


def test_a_clock_value_with_a_non_numeric_half_is_dropped():
    assert _clock("ab:cd") is None


def test_a_boolean_stage_is_rejected_rather_than_read_as_an_int(caplog):
    """``True`` is an int in Python; it is not a stage."""
    parcel = _parcel(2)
    parcel["lifecycle"]["state"] = True
    assert map_parcel_status_at(parcel) is ParcelStatus.UNKNOWN
    assert "lifecycle.state: bool" in caplog.text


def test_a_numeric_string_stage_is_accepted():
    parcel = _parcel(2)
    parcel["lifecycle"]["state"] = "4"
    assert map_parcel_status_at(parcel) is ParcelStatus.OUT_FOR_DELIVERY


def test_a_stage_of_a_nonsense_type_warns_and_degrades(caplog):
    parcel = _parcel(2)
    parcel["lifecycle"]["state"] = ["nope"]
    assert map_parcel_status_at(parcel) is ParcelStatus.UNKNOWN
    assert "lifecycle.state: list" in caplog.text


def test_an_object_state_without_a_delivered_flag_yields_no_stage():
    parcel = _parcel(2)
    parcel["lifecycle"]["state"] = {"text": "Unterwegs"}
    assert map_parcel_status_at(parcel) is ParcelStatus.UNKNOWN


def test_entries_of_the_wrong_shape_warn_and_degrade(caplog):
    parcel = _parcel(2)
    parcel["lifecycle"]["entries"] = "nope"
    assert map_parcel_status_at(parcel) is ParcelStatus.IN_TRANSIT
    assert "lifecycle.entries: str" in caplog.text


def test_an_entry_state_of_the_wrong_shape_warns(caplog):
    parcel = _parcel(2, entries=[{"datetime": _STAMP, "state": ["nope"]}])
    normalize_parcel_at(parcel, include_history=True)
    assert "entries[].state: list" in caplog.text


def test_an_entry_state_given_as_a_string_is_ignored_without_warning(caplog):
    """The renderer reads ``state.text``; a bare string is simply unusable."""
    parcel = _parcel(2, entries=[{"datetime": _STAMP, "state": "Unterwegs"}])
    history = normalize_parcel_at(parcel, include_history=True)["history"]
    assert history == [
        {"timestamp": _STAMP_ISO, "status": ParcelStatus.UNKNOWN, "raw_status": None}
    ]
    assert "entries[].state" not in caplog.text


def test_a_pickup_entry_whose_info_data_is_not_an_object_has_no_name():
    parcel = _parcel(3, entries=[_entry(info_type="shop", info_data="Shop")])
    result = normalize_parcel_at(parcel)
    assert result["status"] is ParcelStatus.AT_PICKUP_POINT
    assert result["pickup_point"] is None


def test_an_absent_predict_at_the_delivery_stage_is_simply_no_window():
    result = normalize_parcel_at(_parcel(4))
    assert result["planned_from"] is None


def test_a_predict_window_with_unreadable_bounds_is_dropped_and_warns(caplog):
    result = normalize_parcel_at(_parcel(4, predict=["99:99", "1300"]))
    assert result["planned_from"] is None
    assert "lifecycle.predict[]" in caplog.text


def test_a_deliver_date_of_the_wrong_shape_warns_and_degrades(caplog):
    parcel = _parcel(5, entries=[], deliver_date="09.10.2026")
    assert normalize_parcel_at(parcel)["delivered_at"] is None
    assert "lifecycle.deliver_date: str" in caplog.text


def test_a_deliver_date_without_a_date_is_dropped():
    parcel = _parcel(5, entries=[], deliver_date={"date": None, "time": "1405"})
    assert normalize_parcel_at(parcel)["delivered_at"] is None


def test_a_deliver_date_in_an_unknown_format_warns_and_degrades(caplog):
    parcel = _parcel(5, entries=[], deliver_date={"date": "9 Oct 2026", "time": "1405"})
    assert normalize_parcel_at(parcel)["delivered_at"] is None
    assert "lifecycle.deliver_date.date" in caplog.text


def test_a_raw_status_that_is_not_a_string_is_dropped():
    parcel = _parcel(2)
    parcel["lifecycle"]["state_info"] = {"unexpected": True}
    assert normalize_parcel_at(parcel)["raw_status"] is None


def test_an_unverified_parcel_never_asks_the_user_to_report_anything(caplog):
    """No lifecycle is normal for an unverified parcel, not a mapping gap."""
    assert (
        map_parcel_status_at({"parcelno": "1", "type": "inc", "verified": False})
        is ParcelStatus.UNKNOWN
    )
    assert caplog.text == ""


def test_a_lifecycle_whose_stage_is_unreadable_does_warn(caplog):
    """The distinction: a lifecycle exists but says nothing usable."""
    assert map_parcel_status_at(_parcel(99)) is ParcelStatus.UNKNOWN
    assert "Unrecognised DPD Austria lifecycle stage" in caplog.text


# --------------------------------------------------------------------------
# The first real Austrian parcel (2026-10-10), scrubbed.
#
# A delivered parcel left at a safe place. Every person/address field was
# already null in the carrier's own response for this unverified parcel; the
# tracking number is replaced anyway. This is the regression anchor for the
# shapes that static reading got wrong.
# --------------------------------------------------------------------------


def _real_parcel() -> dict:
    def entry(stamp, value, name, text, depot="0621", depot_data=None, info=None):
        return {
            "depot": depot,
            "depotData": depot_data,
            "datetime": stamp,
            "type": None,
            "state": {
                "value": value,
                "name": name,
                "additionalInfo": info,
                "text": text,
                "textShort": text,
                "infoType": None,
                "infoData": None,
                "infoDataExtendedOnly": False,
            },
            "extended": None,
            "volume": None,
            "extendedOnly": False,
        }

    return {
        "cincoming_id": "00000000",
        "cusr_id": "0000000",
        "parcelno": "00000000000000",
        "parcel_name": None,
        "date_created": "2026-08-04 19:06:08",
        "sender_name1": None,
        "consignee_name1": None,
        "consignee_addr_postcode": None,
        "consignee_addr_city": None,
        "match_date": None,
        "type": "inc",
        "verified": False,
        "id": 0,
        "verifiedPlz": "",
        # Parcel-level ``state`` is an object here and unrelated to the
        # lifecycle's numeric stage. Its own ``delivered`` is False even for
        # this delivered parcel, so it must not be trusted.
        "state": {
            "delivered": False,
            "redirect": {"addr": False, "neighbour": False},
            "livetracking": False,
            "is2Shop": False,
            "shopID": None,
            "predict": False,
            "statusMessage": "Leider liegen uns derzeit keine Informationen …",
            "lastState": None,
            "state": None,
            "isPrimetime": False,
            # A user preference, NOT a prediction for this parcel.
            "delivery_times": ["08:00", "17:00"],
        },
        "lifecycle": {
            "lang": "de",
            "state": 5,
            "stateV": 5,
            "state_info": "Erfolgreich zugestellt",
            "predict": None,
            "entries": [
                entry("20260804190600", 0, "ORDER_DATA", "Auftragsdaten übermittelt"),
                entry("20260805142800", 2, "ON_THE_WAY", "Paket unterwegs",
                      depot_data=["Leopoldsdorf", "AT"]),
                entry("20260806052600", 3, "IN_DEPOT", "Im Paketzustellzentrum",
                      depot="0635", depot_data=["Seyring", "AT"]),
                entry("20260806053100", 4, "IN_DELIVERY", "In Zustellung",
                      depot="0635"),
                entry("20260806102900", 5, "DELIVERED",
                      "Paket wurde erfolgreich abgestellt", depot="0635",
                      info="ASG"),
            ],
            "isRetoure": False,
            "isShopRetoure": False,
            "receiver": None,
            "hideInfo": False,
            "deliver_date": None,
        },
    }


def test_the_real_parcel_normalizes_end_to_end():
    result = normalize_parcel_at(_real_parcel(), include_history=True)
    assert result["carrier"] == "DPD Austria"
    assert result["status"] is ParcelStatus.DELIVERED
    assert result["raw_status"] == "Erfolgreich zugestellt"
    assert result["delivered"] is True
    # The bug this parcel exposed: 14-digit stamps were dropped, so the
    # delivery time came out null.
    assert result["delivered_at"] == "2026-08-06T10:29:00+02:00"
    assert result["planned_from"] is None
    assert result["planned_to"] is None
    assert result["pickup"] is False
    assert result["weight"] is None
    assert result["dimensions"] is None


def test_the_real_parcel_yields_a_fully_mapped_history():
    history = normalize_parcel_at(_real_parcel(), include_history=True)["history"]
    assert [(item["raw_status"], item["status"]) for item in history] == [
        ("ORDER_DATA", ParcelStatus.REGISTERED),
        ("ON_THE_WAY", ParcelStatus.IN_TRANSIT),
        ("IN_DEPOT", ParcelStatus.IN_TRANSIT),
        ("IN_DELIVERY", ParcelStatus.OUT_FOR_DELIVERY),
        ("DELIVERED", ParcelStatus.DELIVERED),
    ]
    assert history[0]["timestamp"] == "2026-08-04T19:06:00+02:00"


def test_the_real_parcel_logs_nothing_to_report(caplog):
    """A fully understood parcel must not ask the user for anything."""
    normalize_parcel_at(_real_parcel(), include_history=True)
    assert caplog.text == ""


def test_the_parcel_level_delivered_flag_is_not_trusted():
    """It reads False on a delivered parcel; only the lifecycle decides."""
    raw = _real_parcel()
    assert raw["state"]["delivered"] is False
    assert map_parcel_status_at(raw) is ParcelStatus.DELIVERED


def test_the_user_delivery_time_preference_is_never_a_delivery_window():
    """``state.delivery_times`` is a standing preference, not a prediction."""
    raw = _real_parcel()
    raw["lifecycle"]["state"] = 4
    raw["lifecycle"]["entries"] = []
    result = normalize_parcel_at(raw)
    assert result["planned_from"] is None
    assert result["planned_to"] is None


def test_an_unverified_parcel_can_still_carry_a_full_lifecycle():
    """The research assumed otherwise; this real parcel disproves it."""
    raw = _real_parcel()
    assert raw["verified"] is False
    assert normalize_parcel_at(raw)["status"] is ParcelStatus.DELIVERED


def test_the_carrier_return_flags_outrank_the_inbox_direction():
    raw = _real_parcel()
    raw["lifecycle"]["isRetoure"] = True
    raw["lifecycle"]["state"] = 3
    raw["lifecycle"]["entries"] = []
    assert map_parcel_status_at(raw) is ParcelStatus.RETURNING


def test_an_unmapped_status_name_is_reported_once_and_falls_back(caplog):
    """The collector for names a real parcel has not shown us yet."""
    parcel = _parcel(2, entries=[_entry(name="TELEPORTED")])
    assert map_parcel_status_at(parcel) is ParcelStatus.IN_TRANSIT
    assert "unmapped status name" in caplog.text
    assert "TELEPORTED" in caplog.text

    history = normalize_parcel_at(parcel, include_history=True)["history"]
    assert history[0]["status"] is ParcelStatus.UNKNOWN
    assert history[0]["raw_status"] == "TELEPORTED"


def test_the_returns_tab_alone_does_not_make_a_parcel_returning():
    """Regression: the carrier's own false flags outrank the inbox tab.

    The `ret` tab says only where the portal files a parcel. Treating it as
    a return reported `returning` for every parcel in that tab, whatever its
    real stage — the confirmed parcel has both flags explicitly false.
    """
    raw = _real_parcel()
    raw["type"] = "ret"
    raw["lifecycle"]["state"] = 4
    raw["lifecycle"]["entries"] = []
    assert raw["lifecycle"]["isRetoure"] is False
    assert map_parcel_status_at(raw) is ParcelStatus.OUT_FOR_DELIVERY


def test_the_returns_tab_is_the_fallback_when_the_flags_are_absent():
    parcel = _parcel(3, direction="ret")
    assert "isRetoure" not in parcel["lifecycle"]
    assert map_parcel_status_at(parcel) is ParcelStatus.RETURNING
