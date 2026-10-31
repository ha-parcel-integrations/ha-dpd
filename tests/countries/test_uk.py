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


def test_each_distinct_key_set_warns_once_with_keys_only(caplog):
    secret = {"parcelNumber": "15501234567890", "recipientName": "Jane Doe"}
    with caplog.at_level(logging.WARNING):
        normalize_parcel_uk(dict(secret), inactive=False)
        normalize_parcel_uk(dict(secret), inactive=False)
    assert caplog.text.count("parcel shape not seen before") == 1
    assert "recipientName" in caplog.text
    assert "Jane Doe" not in caplog.text
    assert "15501234567890" not in caplog.text


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
