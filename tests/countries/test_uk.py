"""Tests for the DPD UK normalizer and its WARNING collectors."""

import logging

import pytest

from custom_components.dpd.const import ParcelStatus
from custom_components.dpd.countries.uk import (
    build_history_uk,
    map_parcel_status_uk,
    normalize_parcel_uk,
)

# Synthetic: the names and types of a real delivered record, no real value.
DELIVERED = {
    "parcelNumber": "1550 1234 567 890 P",
    "parcelCode": "15501234567890*001",
    "consumerTrackingShortCode": "abc123XYZ",
    "consignmentNumber": "1234567890",
    "parcelStatusType": 2,
    "ivrStatusCode": "1",
    "deliveredToConsumer": True,
    "undelivered": False,
    "collectedFromShop": False,
    "trackingStatusCurrent": "Your parcel has been delivered at 08:15",
    "lastConfirmDate": "2026-10-30 09:00:00",
    "estimatedDeliveryDate": None,
    "estimatedDeliveryStartTime": "00:00",
    "estimatedDeliveryEndTime": "00:00",
    "pickupLocationCode": None,
    "issueDetails": None,
    "collectionCode": None,
    "shipperDetails": {"customerDisplayName": "A Shop", "accountNumber": "1"},
    "deliveryDetails": {
        "numberOfParcels": 1,
        "totalWeight": 2,
        "verifiedWeight": 1.5,
        "address": {"street": "1 Example Road", "postcode": "AB1 2CD"},
        "podDetails": {"podDate": "2026-10-30 08:15:00", "podName": "J DOE"},
    },
}


def _active(**over):
    return {
        "parcelNumber": "15501234567891",
        "parcelCode": "15501234567891*002",
        "deliveredToConsumer": False,
        "undelivered": False,
        "collectedFromShop": False,
        **over,
    }


def test_a_delivered_record_fills_the_canonical_fields():
    parcel = normalize_parcel_uk(dict(DELIVERED), inactive=True)

    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["barcode"] == "15501234567890"
    assert parcel["url"] == "https://track.dpd.co.uk/parcels/15501234567890*001"
    assert parcel["sender"] == "A Shop"
    assert parcel["receiver"] is None
    assert parcel["raw_status"] == "Your parcel has been delivered at 08:15"
    assert parcel["delivered_at"] == "2026-10-30T08:15:00+00:00"
    assert parcel["planned_from"] is None
    assert parcel["planned_to"] is None
    assert parcel["weight"] == 1.5
    assert parcel["pickup"] is False
    assert parcel["history"] is None


def test_raw_keeps_the_record_but_never_a_code_that_opens_something():
    parcel = {**DELIVERED, "collectionCode": "123456", "collectionCodeDisplay": "12 34"}
    raw = normalize_parcel_uk(parcel, inactive=True)["raw"]

    assert "collectionCode" not in raw
    assert "collectionCodeDisplay" not in raw
    assert "consumerTrackingShortCode" not in raw
    assert raw["parcelNumber"] == "1550 1234 567 890 P"
    assert raw["deliveryDetails"] == DELIVERED["deliveryDetails"]


@pytest.mark.parametrize(
    ("kind", "status"),
    [
        (2, ParcelStatus.DELIVERED),
        (3, ParcelStatus.AT_PICKUP_POINT),
        (4, ParcelStatus.IN_TRANSIT),
        (5, ParcelStatus.AT_PICKUP_POINT),
        (6, ParcelStatus.OUT_FOR_DELIVERY),
        (7, ParcelStatus.PROBLEM),
        (8, ParcelStatus.IN_TRANSIT),
    ],
)
def test_each_status_type_maps(kind, status):
    parcel = _active(parcelStatusType=kind)
    assert map_parcel_status_uk(parcel, inactive=False) is status


def test_a_pickup_pass_is_waiting_even_when_dpd_calls_it_delivered():
    parcel = _active(parcelStatusType=5, deliveredToConsumer=True)
    assert map_parcel_status_uk(parcel, inactive=True) is ParcelStatus.AT_PICKUP_POINT


def test_collected_from_the_shop_is_delivered_whatever_the_pass_says():
    parcel = _active(parcelStatusType=5, collectedFromShop=True)
    assert map_parcel_status_uk(parcel, inactive=True) is ParcelStatus.DELIVERED


def test_delivered_flag_beats_an_older_status_type():
    parcel = _active(parcelStatusType=7, deliveredToConsumer=True)
    assert map_parcel_status_uk(parcel, inactive=True) is ParcelStatus.DELIVERED


def test_undelivered_cancels_the_delivered_flag():
    parcel = _active(parcelStatusType=7, deliveredToConsumer=True, undelivered=True)
    assert map_parcel_status_uk(parcel, inactive=True) is ParcelStatus.PROBLEM


@pytest.mark.parametrize(
    ("sentence", "status"),
    [
        (
            "We've received your order details, but have not yet received your parcel",
            ParcelStatus.REGISTERED,
        ),
        ("Your parcel is at our depot", ParcelStatus.IN_TRANSIT),
        ("Your parcel will be with you today", ParcelStatus.OUT_FOR_DELIVERY),
        ("Your delivery has been cancelled", ParcelStatus.PROBLEM),
        ("The parcel has been delivered", ParcelStatus.DELIVERED),
    ],
)
def test_a_type_that_says_nothing_reads_the_sentence(sentence, status):
    parcel = _active(parcelStatusType=0, trackingStatusCurrent=sentence)
    assert map_parcel_status_uk(parcel, inactive=False) is status


def test_a_type_that_says_nothing_settles_on_the_bucket():
    assert (
        map_parcel_status_uk(_active(parcelStatusType=0), inactive=False)
        is ParcelStatus.IN_TRANSIT
    )
    assert (
        map_parcel_status_uk(_active(parcelStatusType=0), inactive=True)
        is ParcelStatus.DELIVERED
    )


def test_an_unknown_type_falls_back_to_the_newest_mappable_event(caplog):
    history = [
        {"timestamp": "t1", "status": ParcelStatus.OUT_FOR_DELIVERY},
        {"timestamp": "t2", "status": ParcelStatus.UNKNOWN},
    ]
    parcel = _active(parcelStatusType=91)
    with caplog.at_level(logging.WARNING):
        status = map_parcel_status_uk(parcel, inactive=False, history=history)

    assert status is ParcelStatus.OUT_FOR_DELIVERY
    assert "Unrecognised DPD UK status" not in caplog.text


def test_an_unknown_type_without_history_is_unknown_and_warned_once(caplog):
    parcel = _active(parcelStatusType=92, trackingStatusCurrent="At 1 Example Road")
    with caplog.at_level(logging.WARNING):
        first = map_parcel_status_uk(parcel, inactive=False)
        map_parcel_status_uk(parcel, inactive=False)

    assert first is ParcelStatus.UNKNOWN
    assert caplog.text.count("Unrecognised DPD UK status") == 1
    assert "parcelStatusType=92" in caplog.text
    assert "Example Road" not in caplog.text


def test_finished_but_undelivered_is_not_called_delivered():
    parcel = _active(parcelStatusType=0, undelivered=True)
    result = normalize_parcel_uk(parcel, inactive=True)
    assert result["status"] is ParcelStatus.UNKNOWN
    assert result["delivered"] is False
    assert result["delivered_at"] is None


def test_delivered_at_falls_back_like_the_app():
    parcel = {
        **DELIVERED,
        "deliveryDetails": {"podDetails": {"podDate": None}},
    }
    assert (
        normalize_parcel_uk(parcel, inactive=True)["delivered_at"]
        == "2026-10-30T09:00:00+00:00"
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-07-01 08:15:00", "2026-07-01T08:15:00+01:00"),
        ("2026-07-01T08:15:00.000000000Z", "2026-07-01T08:15:00+00:00"),
        ("2026-07-01", "2026-07-01T00:00:00+01:00"),
    ],
)
def test_date_forms_read_as_uk_time_unless_they_carry_an_offset(value, expected):
    parcel = {**DELIVERED, "deliveryDetails": {"podDetails": {"podDate": value}}}
    assert normalize_parcel_uk(parcel, inactive=True)["delivered_at"] == expected


def test_an_unparseable_date_is_reported_by_shape_only(caplog):
    parcel = {
        **DELIVERED,
        "deliveryDetails": {"podDetails": {"podDate": "30/10/2026"}},
    }
    with caplog.at_level(logging.WARNING):
        result = normalize_parcel_uk(parcel, inactive=True)
        normalize_parcel_uk(parcel, inactive=True)

    assert result["delivered_at"] is None
    assert caplog.text.count("unparseable date") == 1
    assert "30/10/2026" not in caplog.text


def test_the_window_uses_the_day_and_its_times():
    parcel = _active(
        parcelStatusType=6,
        estimatedDeliveryDate="2026-07-01T00:00:00",
        estimatedDeliveryStartTime="09:30",
        estimatedDeliveryEndTime="10:30:00",
    )
    result = normalize_parcel_uk(parcel, inactive=False)
    assert result["planned_from"] == "2026-07-01T09:30:00+01:00"
    assert result["planned_to"] == "2026-07-01T10:30:00+01:00"


def test_the_window_reads_a_day_month_year_date():
    parcel = _active(
        parcelStatusType=8,
        estimatedDeliveryDate="01/07/2026",
        estimatedDeliveryStartTime="07:00:00",
        estimatedDeliveryEndTime="22:00:59",
    )
    result = normalize_parcel_uk(parcel, inactive=False)
    assert result["planned_from"] == "2026-07-01T07:00:00+01:00"
    assert result["planned_to"] == "2026-07-01T22:00:00+01:00"


@pytest.mark.parametrize(
    ("start", "end"),
    [(None, None), ("00:00", "00:00"), ("10:00", "09:00"), ("25:00", "26:00"), (1, 2)],
)
def test_times_that_make_no_range_leave_the_whole_day(start, end):
    parcel = _active(
        parcelStatusType=8,
        estimatedDeliveryDate="2026-11-01",
        estimatedDeliveryStartTime=start,
        estimatedDeliveryEndTime=end,
    )
    result = normalize_parcel_uk(parcel, inactive=False)
    assert result["planned_from"] == "2026-11-01T00:00:00+00:00"
    assert result["planned_to"] == "2026-11-01T23:59:59+00:00"


def test_an_unparseable_estimated_day_leaves_no_window():
    parcel = _active(parcelStatusType=8, estimatedDeliveryDate="tomorrow")
    result = normalize_parcel_uk(parcel, inactive=False)
    assert result["planned_from"] is None
    assert result["planned_to"] is None


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        ({"numberOfParcels": 1, "verifiedWeight": 0, "totalWeight": 3}, 3.0),
        ({"numberOfParcels": 2, "verifiedWeight": 4, "totalWeight": 8}, None),
        ({"numberOfParcels": 1, "verifiedWeight": True, "totalWeight": 0}, None),
        ({"numberOfParcels": 1, "verifiedWeight": "4"}, None),
    ],
)
def test_weight_is_only_read_for_a_single_parcel(details, expected):
    parcel = _active(parcelStatusType=8, deliveryDetails=details)
    assert normalize_parcel_uk(parcel, inactive=False)["weight"] == expected


@pytest.mark.parametrize(
    "over",
    [{"parcelStatusType": 4}, {"parcelStatusType": 5}, {"pickupLocationCode": "S1"}],
)
def test_a_pickup_parcel_is_flagged(over):
    assert normalize_parcel_uk(_active(**over), inactive=False)["pickup"] is True


@pytest.mark.parametrize(
    ("number", "code", "barcode"),
    [
        ("1550 1234 567 890 P", "15501234567890*001", "15501234567890"),
        ("1550 1234 567 890 P", None, "15501234567890"),
        ("1550 1234 567 890 7", "garbled", "15501234567890"),
        ("15501234567890", None, "15501234567890"),
        ("AB 12", None, "AB12"),
        ("   ", None, None),
    ],
)
def test_the_barcode_is_the_bare_parcel_number(number, code, barcode):
    parcel = {"parcelNumber": number, "parcelCode": code, "parcelStatusType": 0}
    assert normalize_parcel_uk(parcel, inactive=False)["barcode"] == barcode


def test_missing_identifiers_leave_barcode_url_and_sender_unset():
    parcel = normalize_parcel_uk({"parcelStatusType": 0}, inactive=False)
    assert parcel["barcode"] is None
    assert parcel["url"] is None
    assert parcel["sender"] is None
    assert parcel["raw_status"] is None
    assert parcel["weight"] is None


def test_the_confirmed_shape_stays_quiet_whatever_is_null(caplog):
    with caplog.at_level(logging.WARNING):
        normalize_parcel_uk(dict(DELIVERED), inactive=True)
        normalize_parcel_uk({**DELIVERED, "collectionDetails": None}, inactive=True)
    assert "not seen before" not in caplog.text


def test_new_fields_warn_once_with_names_and_types_only(caplog):
    parcel = {
        **DELIVERED,
        "issueDetails": {"issueCode": "ZZ-secret", "issueDelayDays": 2},
        "newList": [{"town": "Leeds"}],
        "newEmpty": [],
        "a": {"b": {"c": {"d": "too far"}}},
    }
    with caplog.at_level(logging.WARNING):
        normalize_parcel_uk(dict(parcel), inactive=True)
        normalize_parcel_uk(dict(parcel), inactive=True)

    assert caplog.text.count("not seen before") == 1
    assert "issueDetails.issueCode:str" in caplog.text
    assert "issueDetails.issueDelayDays:int" in caplog.text
    assert "newList[].town:str" in caplog.text
    assert "newEmpty:list" in caplog.text
    assert "a.b.c:dict" in caplog.text
    for value in ("ZZ-secret", "Leeds", "too far", "15501234567890"):
        assert value not in caplog.text


def test_history_is_sorted_capped_and_keeps_the_code_not_the_sentence():
    events = [
        {
            "eventCode": "DLV",
            "eventDate": "2026-07-02 08:00:00",
            "eventLocation": "Depot",
            "eventText": "Your parcel has been delivered to 1 Example Road",
        },
        {
            "eventCode": "OFD",
            "eventDate": "2026-07-01 08:00:00",
            "eventText": "The parcel is on the vehicle for delivery",
        },
        {"eventCode": "OLD", "eventDate": None, "eventText": "No date"},
    ]
    history = build_history_uk(events)
    assert history == [
        {
            "timestamp": "2026-07-01T08:00:00+01:00",
            "status": ParcelStatus.OUT_FOR_DELIVERY,
            "raw_status": "OFD",
        },
        {
            "timestamp": "2026-07-02T08:00:00+01:00",
            "status": ParcelStatus.DELIVERED,
            "raw_status": "DLV",
        },
    ]
    assert build_history_uk(events, max_events=1) == history[-1:]


def test_an_unmapped_event_is_unknown_and_reported_by_its_code_once(caplog):
    event = {
        "eventCode": 77,
        "eventDate": "2026-07-01 08:00:00",
        "eventText": "Handed to J Doe at 1 Example Road",
    }
    with caplog.at_level(logging.WARNING):
        history = build_history_uk([event, dict(event)])

    assert history[0]["status"] is ParcelStatus.UNKNOWN
    assert history[0]["raw_status"] == "77"
    assert caplog.text.count("Unrecognised DPD UK parcel event") == 1
    assert "eventCode=77" in caplog.text
    assert "Example Road" not in caplog.text


def test_an_event_without_a_code_keeps_no_raw_status():
    event = {
        "eventDate": "2026-07-01 08:00:00",
        "eventText": "Your parcel is at our depot",
    }
    assert build_history_uk([event])[0]["raw_status"] is None
