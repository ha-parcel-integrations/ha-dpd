"""Tests for DPD Polska canonical normalization."""
from custom_components.dpd.const import ParcelStatus
from custom_components.dpd.countries.pl import (
    _timestamp,
    map_parcel_status_pl,
    normalize_parcel_pl,
)


def _parcel(status: str = "HANDED_OVER_FOR_DELIVERY") -> dict:
    return {
        "waybill": "12345678901234",
        "sender": {"name": "Example Shop", "address": {"city": "Warsaw"}},
        "main_status": {"status": status, "date": "2026-09-09T08:00:00Z"},
        "statuses": [{"status": "READY_TO_SEND", "date": "2026-09-08T10:00:00Z"}],
        "delivery": {"planned_delivery_date": "2026-09-10", "delivered_datetime": None},
        "is_manageable": True,
        "user_actions": [{"code": "MANAGE_PACKAGE", "validation_token": "secret"}],
        "mps": {"current_parcel_number": 1, "parcels_count": 2, "parcels": [{"waybill": "other"}]},
    }


def test_normalize_maps_canonical_fields_and_keeps_the_full_raw_response():
    parcel = normalize_parcel_pl(_parcel())
    assert parcel["carrier"] == "DPD Polska"
    assert parcel["barcode"] == "12345678901234"
    assert parcel["sender"] == "Example Shop"
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["planned_from"] == "2026-09-10T00:00:00"
    assert parcel["history"] is None
    assert parcel["raw"] == _parcel()


def test_normalize_includes_history_only_when_requested():
    parcel = normalize_parcel_pl(_parcel(), include_history=True)
    assert parcel["history"] == [{
        "timestamp": "2026-09-08T10:00:00+00:00",
        "status": ParcelStatus.REGISTERED,
        "raw_status": "READY_TO_SEND",
    }]


def test_normalize_accepts_the_apk_create_time_timestamp_alias():
    raw = _parcel()
    raw["statuses"] = [{"status": "READY_TO_SEND", "create_time": "2026-09-08T10:00:00Z"}]
    parcel = normalize_parcel_pl(raw, include_history=True)
    assert parcel["history"][0]["timestamp"] == "2026-09-08T10:00:00+00:00"


def test_status_map_covers_terminal_and_unknown_values():
    assert map_parcel_status_pl("PICKED_UP") is ParcelStatus.DELIVERED
    assert map_parcel_status_pl("RETURNED_TO_SENDER") is ParcelStatus.RETURNING
    assert map_parcel_status_pl("UNSUCCESSFUL_DELIVERY") is ParcelStatus.PROBLEM
    assert map_parcel_status_pl("FUTURE_STATUS") is ParcelStatus.UNKNOWN


def test_timestamp_returns_none_for_missing_or_non_string_values():
    assert _timestamp(None) is None
    assert _timestamp("") is None
    assert _timestamp(12345) is None


def test_timestamp_logs_once_and_returns_none_for_unparseable_shape(caplog):
    with caplog.at_level("WARNING"):
        assert _timestamp("not-a-timestamp") is None
        assert _timestamp("not-a-timestamp") is None
    assert sum("unparseable timestamp" in r.message for r in caplog.records) == 1


def test_normalize_warns_once_on_unexpected_main_status_shape(caplog):
    raw = _parcel()
    raw["main_status"] = "not-a-dict"
    with caplog.at_level("WARNING"):
        parcel = normalize_parcel_pl(raw)
        normalize_parcel_pl(raw)
    assert parcel["status"] is ParcelStatus.UNKNOWN
    assert sum("unexpected payload shape" in r.message for r in caplog.records) == 1


def test_normalize_warns_once_on_unexpected_delivery_shape(caplog):
    raw = _parcel()
    raw["delivery"] = "not-a-dict"
    with caplog.at_level("WARNING"):
        parcel = normalize_parcel_pl(raw)
    assert parcel["planned_from"] is None
    assert any("unexpected payload shape" in r.message for r in caplog.records)


def test_normalize_warns_once_on_unexpected_statuses_shape(caplog):
    raw = _parcel()
    raw["statuses"] = "not-a-list"
    with caplog.at_level("WARNING"):
        parcel = normalize_parcel_pl(raw, include_history=True)
    assert parcel["history"] == []
    assert any("unexpected payload shape" in r.message for r in caplog.records)
