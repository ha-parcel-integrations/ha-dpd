"""Tests for the coarse DPD UK normalizer and its WARNING collectors."""

import logging

from custom_components.dpd.const import ParcelStatus
from custom_components.dpd.countries.uk import normalize_parcel_uk

PARCEL = {"parcelNumber": "15501234567890", "parcelCode": "15501234567890*001"}


def test_active_bucket_is_in_progress_and_inactive_is_delivered():
    active = normalize_parcel_uk(dict(PARCEL), inactive=False)
    done = normalize_parcel_uk(dict(PARCEL), inactive=True)

    assert active["status"] is ParcelStatus.IN_TRANSIT
    assert active["delivered"] is False
    assert done["status"] is ParcelStatus.DELIVERED
    assert done["delivered"] is True
    assert active["barcode"] == "15501234567890"
    assert active["url"] == "https://track.dpd.co.uk/parcels/15501234567890*001"
    assert active["history"] is None
    assert active["raw"] == PARCEL


def test_history_is_an_empty_list_when_requested_and_fields_are_unset():
    parcel = normalize_parcel_uk(dict(PARCEL), inactive=False, include_history=True)
    assert parcel["history"] == []
    for key in ("sender", "receiver", "planned_from", "planned_to", "delivered_at"):
        assert parcel[key] is None
    assert parcel["pickup"] is False


def test_missing_identifiers_leave_barcode_and_url_unset():
    parcel = normalize_parcel_uk({"other": 1}, inactive=False)
    assert parcel["barcode"] is None
    assert parcel["url"] is None


def test_each_distinct_shape_warns_once_with_names_and_types_only(caplog):
    secret = {
        "parcelNumber": "15501234567890",
        "recipientName": "Jane Doe",
        "deliveryDetails": {
            "date": "2026-11-01",
            "window": {"from": "09:00", "to": "12:00"},
        },
        "events": [{"code": "OFD", "town": "Leeds"}],
        "weightKg": 1.5,
    }
    with caplog.at_level(logging.WARNING):
        normalize_parcel_uk(dict(secret), inactive=False)
        normalize_parcel_uk(dict(secret), inactive=False)

    assert caplog.text.count("parcel shape not seen before") == 1
    # The nested field names and their types are what finishes the normalizer.
    assert '"recipientName": "str"' in caplog.text
    assert '"weightKg": "float"' in caplog.text
    assert '"window": {"from": "str", "to": "str"}' in caplog.text
    assert '"events": [{"code": "str", "town": "str"}]' in caplog.text
    for value in ("Jane Doe", "15501234567890", "2026-11-01", "Leeds", "1.5"):
        assert value not in caplog.text


def test_the_sketch_stops_descending_and_an_empty_list_stays_empty(caplog):
    deep = {"a": {"b": {"c": {"d": {"e": "too far"}}}}, "events": []}
    with caplog.at_level(logging.WARNING):
        normalize_parcel_uk(deep, inactive=False)

    assert '"c": "{...}"' in caplog.text
    assert '"events": []' in caplog.text
    assert "too far" not in caplog.text


def test_a_differently_spelled_status_is_still_reported(caplog):
    parcel = {**PARCEL, "parcelState": "OUT_FOR_DELIVERY"}
    with caplog.at_level(logging.WARNING):
        result = normalize_parcel_uk(dict(parcel), inactive=False)

    assert result["raw_status"] == "OUT_FOR_DELIVERY"
    assert "parcelState='OUT_FOR_DELIVERY'" in caplog.text


def test_a_timestamp_beside_the_status_is_not_mistaken_for_it():
    parcel = {**PARCEL, "statusDate": "2026-11-01T10:00:00Z"}
    assert normalize_parcel_uk(dict(parcel), inactive=False)["raw_status"] is None


def test_a_status_string_is_kept_and_warned_once(caplog):
    parcel = {**PARCEL, "status": "OUT_FOR_DELIVERY"}
    with caplog.at_level(logging.WARNING):
        first = normalize_parcel_uk(dict(parcel), inactive=False)
        normalize_parcel_uk(dict(parcel), inactive=False)
    assert first["raw_status"] == "OUT_FOR_DELIVERY"
    assert first["status"] is ParcelStatus.IN_TRANSIT
    assert caplog.text.count("Unrecognised DPD UK status") == 1


def test_a_non_string_status_is_ignored():
    assert (
        normalize_parcel_uk({"status": {"x": 1}}, inactive=False)["raw_status"] is None
    )
