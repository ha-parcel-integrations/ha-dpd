"""Tests for the DPD coordinator filter functions, data shape and error handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dpd.api import DpdApiError, DpdAuthError
from custom_components.dpd.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
)
from custom_components.dpd.const import ParcelStatus
from custom_components.dpd.coordinator import (
    DpdCoordinator,
    _tracking_url,
    _unknown_descriptions_logged,
    annotate_planned_delivery,
    filter_active_shipments,
    filter_delivered_shipments,
    fmp_hashcode,
    log_unknown_descriptions,
    map_parcel_status,
    normalize_parcel,
    shipment_delivery_dt,
    shipment_planned_dt,
    shipment_planned_window,
)


def _mock_entry(filter_type: str = "days", filter_amount: int = 7) -> MagicMock:
    entry = MagicMock()
    entry.options = {
        CONF_DELIVERED_FILTER_TYPE: filter_type,
        CONF_DELIVERED_FILTER_AMOUNT: filter_amount,
    }
    return entry


def _shipment(
    description: str = "DELIVERED",
    parcel_number: str = "01XXXXXXXXXXXX",
    event_dt: str | None = None,
    tz_id: str | None = "Europe/Amsterdam",
    delivery_date: str | None = None,
    fmp_hashcode: str | None = None,
    fmp_window: dict | None = None,
) -> dict:
    status: dict = {"description": description}
    if event_dt is not None:
        status["eventDateAndTime"] = event_dt
    if tz_id is not None:
        status["eventDateAndTimeZoneId"] = tz_id
    out: dict = {"parcelNumber": parcel_number, "status": status}
    if delivery_date is not None:
        out["deliveryDate"] = delivery_date
    if fmp_hashcode is not None:
        out["availableActions"] = {"FOLLOW_MY_PARCEL": [{"hashcode": fmp_hashcode}]}
    if fmp_window is not None:
        out["fmpDeliveryDateAndTime"] = fmp_window
    return out


# ---------------------------------------------------------------------------
# filter_active_shipments / filter_delivered_shipments
# ---------------------------------------------------------------------------


def test_active_filter_excludes_delivered():
    assert filter_active_shipments([_shipment("DELIVERED")]) == []


def test_active_filter_includes_order_created():
    assert filter_active_shipments([_shipment("ORDER_CREATED")]) != []


def test_active_filter_includes_unknown_status():
    assert filter_active_shipments([_shipment("OUT_FOR_DELIVERY")]) != []


def test_active_filter_handles_missing_status():
    assert filter_active_shipments([{"parcelNumber": "X"}]) != []


def test_delivered_filter_only_includes_delivered():
    shipments = [_shipment("DELIVERED"), _shipment("ORDER_CREATED")]
    result = filter_delivered_shipments(shipments)
    assert len(result) == 1
    assert (result[0]["status"] or {}).get("description") == "DELIVERED"


# ---------------------------------------------------------------------------
# shipment_delivery_dt
# ---------------------------------------------------------------------------


def test_delivery_dt_parses_event_datetime_with_tz():
    dt = shipment_delivery_dt(
        _shipment(event_dt="2026-06-05T21:50:30", tz_id="Europe/Amsterdam")
    )
    assert dt is not None
    assert dt.year == 2026 and dt.month == 6 and dt.day == 5
    assert dt.tzinfo is not None


def test_delivery_dt_falls_back_to_delivery_date():
    dt = shipment_delivery_dt(
        _shipment(event_dt=None, tz_id=None, delivery_date="2026-06-05")
    )
    assert dt == datetime(2026, 6, 5, tzinfo=timezone.utc)


def test_delivery_dt_returns_none_when_unknown():
    assert shipment_delivery_dt(_shipment(event_dt=None, tz_id=None)) is None


def test_delivery_dt_invalid_tz_falls_back_to_utc():
    dt = shipment_delivery_dt(
        _shipment(event_dt="2026-06-05T21:50:30", tz_id="Not/A/Zone")
    )
    assert dt is not None
    assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# shipment_planned_dt
# ---------------------------------------------------------------------------


def test_planned_dt_parses_delivery_date_at_local_midnight():
    dt = shipment_planned_dt(
        _shipment(event_dt=None, tz_id="Europe/Amsterdam", delivery_date="2026-06-17")
    )
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2026, 6, 17)
    assert (dt.hour, dt.minute) == (0, 0)
    assert dt.tzinfo is not None
    # 00:00 Europe/Amsterdam is offset from UTC
    assert dt.utcoffset() is not None


def test_planned_dt_returns_none_when_no_date():
    assert shipment_planned_dt(_shipment(event_dt=None, tz_id=None)) is None


def test_planned_dt_returns_none_for_garbage_date():
    assert shipment_planned_dt({"deliveryDate": "not a date"}) is None


def test_planned_dt_falls_back_to_utc_for_bad_tz():
    dt = shipment_planned_dt(
        _shipment(event_dt=None, tz_id="Not/A/Zone", delivery_date="2026-06-17")
    )
    assert dt is not None
    assert dt.tzinfo is not None


# ---------------------------------------------------------------------------
# DpdCoordinator._apply_delivered_filter
# ---------------------------------------------------------------------------


async def test_delivered_filter_days_excludes_old_parcels(hass):
    recent_date = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()
    shipments = [
        _shipment(event_dt=None, tz_id=None, delivery_date=recent_date),
        _shipment(event_dt=None, tz_id=None, delivery_date=old_date),
    ]

    coordinator = DpdCoordinator(hass, MagicMock(), _mock_entry("days", 7))
    result = coordinator._apply_delivered_filter(shipments)
    assert len(result) == 1
    assert result[0]["deliveryDate"] == recent_date


async def test_delivered_filter_days_includes_parcel_without_date(hass):
    shipments = [_shipment(event_dt=None, tz_id=None)]
    coordinator = DpdCoordinator(hass, MagicMock(), _mock_entry("days", 7))
    assert len(coordinator._apply_delivered_filter(shipments)) == 1


async def test_delivered_filter_parcels_limits_count(hass):
    shipments = [_shipment(parcel_number=f"P{i}") for i in range(10)]
    coordinator = DpdCoordinator(hass, MagicMock(), _mock_entry("parcels", 3))
    result = coordinator._apply_delivered_filter(shipments)
    assert len(result) == 3


# ---------------------------------------------------------------------------
# DpdCoordinator._async_update_data
# ---------------------------------------------------------------------------


async def test_coordinator_splits_active_and_delivered(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value={
        "incomingShipments": [
            _shipment("ORDER_CREATED", parcel_number="A"),
            _shipment("DELIVERED", parcel_number="B"),
        ],
        "sendingShipments": [_shipment("DELIVERED", parcel_number="C")],
    })

    coordinator = DpdCoordinator(hass, client, _mock_entry("days", 30))
    result = await coordinator._async_update_data()

    assert [s["barcode"] for s in result["incoming_active"]] == ["A"]
    assert [s["barcode"] for s in result["incoming_delivered"]] == ["B"]
    # Outgoing delivered shipments are dropped entirely.
    assert result["outgoing_active"] == []


async def test_coordinator_handles_empty_response(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value={
        "incomingShipments": [],
        "sendingShipments": [],
    })

    coordinator = DpdCoordinator(hass, client, _mock_entry())
    assert await coordinator._async_update_data() == {
        "incoming_active": [],
        "incoming_delivered": [],
        "outgoing_active": [],
    }


async def test_coordinator_raises_config_entry_auth_failed_on_auth_error(hass):
    from homeassistant.exceptions import ConfigEntryAuthFailed

    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=DpdAuthError("bad creds"))

    coordinator = DpdCoordinator(hass, client, _mock_entry())

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_coordinator_raises_update_failed_on_api_error(hass):
    from homeassistant.helpers.update_coordinator import UpdateFailed

    client = MagicMock()
    client.async_get_parcels = AsyncMock(side_effect=DpdApiError(500))

    coordinator = DpdCoordinator(hass, client, _mock_entry())

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


# ---------------------------------------------------------------------------
# log_unknown_descriptions
# ---------------------------------------------------------------------------


def test_known_descriptions_are_not_logged(caplog):
    _unknown_descriptions_logged.clear()
    caplog.set_level("INFO", logger="custom_components.dpd.coordinator")
    log_unknown_descriptions([
        _shipment("ORDER_CREATED"),
        _shipment("PARCEL_OUT_FOR_DELIVERY"),
        _shipment("DELIVERED"),
    ])
    assert "not yet catalogued" not in caplog.text


def test_unknown_description_is_logged_once(caplog):
    _unknown_descriptions_logged.clear()
    caplog.set_level("INFO", logger="custom_components.dpd.coordinator")
    log_unknown_descriptions([_shipment("TIME_TRAVELLING")])
    log_unknown_descriptions([_shipment("TIME_TRAVELLING")])
    log_unknown_descriptions([_shipment("TIME_TRAVELLING")])
    assert caplog.text.count("TIME_TRAVELLING") == 1


def test_unknown_descriptions_each_logged_once(caplog):
    _unknown_descriptions_logged.clear()
    caplog.set_level("INFO", logger="custom_components.dpd.coordinator")
    log_unknown_descriptions([
        _shipment("MYSTERY_ONE"),
        _shipment("MYSTERY_TWO"),
        _shipment("MYSTERY_ONE"),  # repeat — should not log again
    ])
    assert caplog.text.count("MYSTERY_ONE") == 1
    assert caplog.text.count("MYSTERY_TWO") == 1


def test_missing_description_is_ignored(caplog):
    _unknown_descriptions_logged.clear()
    caplog.set_level("INFO", logger="custom_components.dpd.coordinator")
    log_unknown_descriptions([{"parcelNumber": "X"}, {"parcelNumber": "Y", "status": {}}])
    assert "not yet catalogued" not in caplog.text


# ---------------------------------------------------------------------------
# fmp_hashcode
# ---------------------------------------------------------------------------


def test_fmp_hashcode_picks_from_available_actions():
    assert fmp_hashcode(_shipment(fmp_hashcode="abc")) == "abc"


def test_fmp_hashcode_returns_none_when_action_missing():
    assert fmp_hashcode(_shipment()) is None


def test_fmp_hashcode_returns_none_when_actions_empty():
    shipment = _shipment()
    shipment["availableActions"] = {"FOLLOW_MY_PARCEL": []}
    assert fmp_hashcode(shipment) is None


def test_fmp_hashcode_returns_none_when_action_has_no_hashcode():
    shipment = _shipment()
    shipment["availableActions"] = {"FOLLOW_MY_PARCEL": [{}]}
    assert fmp_hashcode(shipment) is None


def test_fmp_hashcode_returns_none_for_empty_string():
    assert fmp_hashcode(_shipment(fmp_hashcode="")) is None


# ---------------------------------------------------------------------------
# shipment_planned_dt with Follow My Parcel window
# ---------------------------------------------------------------------------


def test_planned_dt_prefers_fmp_window_over_date_midnight():
    fmp = {
        "deliveryDate": "2026-06-17",
        "timeRange": {"from": "10:34:00", "to": "11:34:00"},
    }
    dt = shipment_planned_dt(_shipment(
        delivery_date="2026-06-17",
        tz_id="Europe/Amsterdam",
        fmp_window=fmp,
    ))
    assert dt is not None
    assert (dt.hour, dt.minute, dt.second) == (10, 34, 0)
    assert dt.tzinfo is not None


def test_planned_dt_falls_back_to_midnight_when_fmp_window_lacks_from():
    fmp = {"deliveryDate": "2026-06-17", "timeRange": {}}
    dt = shipment_planned_dt(_shipment(
        delivery_date="2026-06-17",
        tz_id="Europe/Amsterdam",
        fmp_window=fmp,
    ))
    assert dt is not None
    assert (dt.hour, dt.minute) == (0, 0)


def test_planned_dt_falls_back_to_midnight_when_fmp_from_is_garbage():
    fmp = {"deliveryDate": "2026-06-17", "timeRange": {"from": "not-a-time"}}
    dt = shipment_planned_dt(_shipment(
        delivery_date="2026-06-17",
        tz_id="Europe/Amsterdam",
        fmp_window=fmp,
    ))
    assert dt is not None
    assert (dt.hour, dt.minute) == (0, 0)


# ---------------------------------------------------------------------------
# DpdCoordinator._enrich_with_fmp
# ---------------------------------------------------------------------------


async def test_enrich_with_fmp_skips_shipments_without_hashcode(hass):
    client = MagicMock()
    client.async_fmp_delivery_window = AsyncMock(return_value={"deliveryDate": "x"})
    coordinator = DpdCoordinator(hass, client, _mock_entry())

    await coordinator._enrich_with_fmp([_shipment("PARCEL_HANDED")])

    client.async_fmp_delivery_window.assert_not_called()


async def test_enrich_with_fmp_stores_window_on_shipment(hass):
    window = {
        "deliveryDate": "2026-06-17",
        "timeRange": {"from": "10:34:00", "to": "11:34:00"},
    }
    client = MagicMock()
    client.async_fmp_delivery_window = AsyncMock(return_value=window)
    coordinator = DpdCoordinator(hass, client, _mock_entry())

    shipment = _shipment("PARCEL_OUT_FOR_DELIVERY", fmp_hashcode="xyz123")
    await coordinator._enrich_with_fmp([shipment])

    client.async_fmp_delivery_window.assert_awaited_once_with("xyz123")
    assert shipment["fmpDeliveryDateAndTime"] == window


async def test_enrich_with_fmp_leaves_shipment_alone_when_window_unavailable(hass):
    client = MagicMock()
    client.async_fmp_delivery_window = AsyncMock(return_value=None)
    coordinator = DpdCoordinator(hass, client, _mock_entry())

    shipment = _shipment("PARCEL_OUT_FOR_DELIVERY", fmp_hashcode="xyz123")
    await coordinator._enrich_with_fmp([shipment])

    assert "fmpDeliveryDateAndTime" not in shipment


# ---------------------------------------------------------------------------
# map_parcel_status
# ---------------------------------------------------------------------------


def test_map_status_order_created_is_registered():
    assert map_parcel_status(_shipment("ORDER_CREATED")) == ParcelStatus.REGISTERED


def test_map_status_handed_in_transit_at_center_all_map_to_in_transit():
    assert map_parcel_status(_shipment("PARCEL_HANDED")) == ParcelStatus.IN_TRANSIT
    assert map_parcel_status(_shipment("IN_TRANSIT")) == ParcelStatus.IN_TRANSIT
    assert map_parcel_status(_shipment("AT_DELIVERY_CENTER")) == ParcelStatus.IN_TRANSIT


def test_map_status_parcel_out_for_delivery_is_out_for_delivery():
    assert (
        map_parcel_status(_shipment("PARCEL_OUT_FOR_DELIVERY"))
        == ParcelStatus.OUT_FOR_DELIVERY
    )


def test_map_status_delivered_is_delivered():
    assert map_parcel_status(_shipment("DELIVERED")) == ParcelStatus.DELIVERED


def test_map_status_unknown_description_falls_back_to_unknown():
    assert map_parcel_status(_shipment("INVENTED_BY_DPD")) == ParcelStatus.UNKNOWN


def test_map_status_missing_status_field_falls_back_to_unknown():
    assert map_parcel_status({"parcelNumber": "X"}) == ParcelStatus.UNKNOWN


# ---------------------------------------------------------------------------
# _tracking_url
# ---------------------------------------------------------------------------


def test_tracking_url_built_from_parcel_number():
    assert _tracking_url({"parcelNumber": "01ABC"}) == (
        "https://www.dpdgroup.com/nl/mydpd/my-parcels/search?parcelNumber=01ABC"
    )


def test_tracking_url_returns_none_without_parcel_number():
    assert _tracking_url({}) is None
    assert _tracking_url({"parcelNumber": ""}) is None


# ---------------------------------------------------------------------------
# normalize_parcel
# ---------------------------------------------------------------------------


def test_normalize_returns_carrier_agnostic_keys():
    raw = _shipment("PARCEL_OUT_FOR_DELIVERY", parcel_number="01XYZ")
    raw["senderName"] = "Acme Webshop"
    raw["status"]["deliveryType"] = "HOME"
    normalized = normalize_parcel(raw)
    assert normalized["carrier"] == "DPD"
    assert normalized["barcode"] == "01XYZ"
    assert normalized["sender"] == "Acme Webshop"
    assert normalized["status"] == ParcelStatus.OUT_FOR_DELIVERY
    assert normalized["raw_status"] == "PARCEL_OUT_FOR_DELIVERY"
    assert normalized["delivered"] is False
    assert normalized["pickup"] is False
    assert normalized["pickup_point"] is None
    assert normalized["url"].endswith("parcelNumber=01XYZ")
    assert normalized["raw"] is raw  # original payload preserved by identity


def test_normalize_marks_pickup_for_parcelshop_delivery():
    raw = _shipment("PARCEL_OUT_FOR_DELIVERY")
    raw["status"]["deliveryType"] = "PARCELSHOP"
    assert normalize_parcel(raw)["pickup"] is True


def test_normalize_delivered_parcel_carries_delivered_at_not_planned_window():
    raw = _shipment(
        "DELIVERED",
        delivery_date="2026-06-05",
        event_dt="2026-06-05T14:23:12",
        tz_id="Europe/Amsterdam",
    )
    annotate_planned_delivery(raw)
    normalized = normalize_parcel(raw)
    assert normalized["delivered"] is True
    assert normalized["delivered_at"] is not None
    assert "2026-06-05T14:23:12" in normalized["delivered_at"]
    assert normalized["planned_from"] is None
    assert normalized["planned_to"] is None


def test_normalize_active_parcel_carries_planned_window_from_annotation():
    raw = _shipment(
        "PARCEL_OUT_FOR_DELIVERY",
        delivery_date="2026-06-17",
        tz_id="Europe/Amsterdam",
        fmp_window={
            "deliveryDate": "2026-06-17",
            "timeRange": {"from": "10:34:00", "to": "11:34:00"},
        },
    )
    annotate_planned_delivery(raw)
    normalized = normalize_parcel(raw)
    assert normalized["planned_from"].startswith("2026-06-17T10:34:00")
    assert normalized["planned_to"].startswith("2026-06-17T11:34:00")


# ---------------------------------------------------------------------------
# shipment_planned_window (from, to)
# ---------------------------------------------------------------------------


def test_planned_window_returns_fmp_range_when_available():
    fmp = {
        "deliveryDate": "2026-06-17",
        "timeRange": {"from": "10:34:00", "to": "11:34:00"},
    }
    start, end = shipment_planned_window(_shipment(
        delivery_date="2026-06-17",
        tz_id="Europe/Amsterdam",
        fmp_window=fmp,
    ))
    assert start is not None and end is not None
    assert (start.hour, start.minute) == (10, 34)
    assert (end.hour, end.minute) == (11, 34)
    assert start.tzinfo is not None
    assert end.tzinfo is not None


def test_planned_window_full_day_when_only_date_known():
    start, end = shipment_planned_window(_shipment(
        delivery_date="2026-06-17",
        tz_id="Europe/Amsterdam",
    ))
    assert start is not None and end is not None
    assert (start.hour, start.minute, start.second) == (0, 0, 0)
    assert (end.hour, end.minute, end.second) == (23, 59, 59)
    assert start.date() == end.date()


def test_planned_window_returns_none_when_no_date():
    assert shipment_planned_window({}) == (None, None)


def test_planned_window_full_day_when_fmp_lacks_from_or_to():
    fmp = {"deliveryDate": "2026-06-17", "timeRange": {"from": "10:34:00"}}
    start, end = shipment_planned_window(_shipment(
        delivery_date="2026-06-17",
        tz_id="Europe/Amsterdam",
        fmp_window=fmp,
    ))
    # No `to` in FMP → fall back to the full-day window
    assert (start.hour, end.hour) == (0, 23)


# ---------------------------------------------------------------------------
# annotate_planned_delivery
# ---------------------------------------------------------------------------


def test_annotate_writes_iso_strings_with_tz():
    shipment = _shipment(delivery_date="2026-06-17", tz_id="Europe/Amsterdam")
    annotate_planned_delivery(shipment)
    assert shipment["plannedDeliveryFrom"].startswith("2026-06-17T00:00:00")
    assert shipment["plannedDeliveryTo"].startswith("2026-06-17T23:59:59")
    # Both carry an offset (not bare naive iso)
    assert "+" in shipment["plannedDeliveryFrom"]
    assert "+" in shipment["plannedDeliveryTo"]


def test_annotate_uses_fmp_window_when_present():
    fmp = {
        "deliveryDate": "2026-06-17",
        "timeRange": {"from": "10:34:00", "to": "11:34:00"},
    }
    shipment = _shipment(
        delivery_date="2026-06-17",
        tz_id="Europe/Amsterdam",
        fmp_window=fmp,
    )
    annotate_planned_delivery(shipment)
    assert shipment["plannedDeliveryFrom"].startswith("2026-06-17T10:34:00")
    assert shipment["plannedDeliveryTo"].startswith("2026-06-17T11:34:00")


def test_annotate_sets_none_when_no_date():
    shipment = {"parcelNumber": "X", "status": {"description": "ORDER_CREATED"}}
    annotate_planned_delivery(shipment)
    assert shipment["plannedDeliveryFrom"] is None
    assert shipment["plannedDeliveryTo"] is None


# ---------------------------------------------------------------------------
# Coordinator wires annotation onto every bucket
# ---------------------------------------------------------------------------


async def test_coordinator_annotates_all_buckets_with_planned_window(hass):
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value={
        "incomingShipments": [
            _shipment("ORDER_CREATED", parcel_number="A", delivery_date="2026-06-17"),
            _shipment("DELIVERED", parcel_number="B", delivery_date="2026-06-10"),
        ],
        "sendingShipments": [
            _shipment("PARCEL_HANDED", parcel_number="C", delivery_date="2026-06-18"),
        ],
    })
    client.async_fmp_delivery_window = AsyncMock(return_value=None)

    coordinator = DpdCoordinator(hass, client, _mock_entry("days", 30))
    result = await coordinator._async_update_data()

    # Active buckets carry the from/to on top-level normalised fields;
    # delivered parcels expose them under `raw` only (planned_* is None
    # because delivered_at carries the truth instead).
    for parcel in result["incoming_active"] + result["outgoing_active"]:
        assert parcel["planned_from"] is not None
        assert parcel["planned_to"] is not None
    for parcel in result["incoming_delivered"]:
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None
        assert parcel["raw"]["plannedDeliveryFrom"] is not None


async def test_coordinator_calls_fmp_for_eligible_shipments(hass):
    window = {
        "deliveryDate": "2026-06-17",
        "timeRange": {"from": "10:34:00", "to": "11:34:00"},
    }
    client = MagicMock()
    client.async_get_parcels = AsyncMock(return_value={
        "incomingShipments": [
            _shipment("PARCEL_OUT_FOR_DELIVERY", parcel_number="A", fmp_hashcode="hashA"),
            _shipment("PARCEL_HANDED", parcel_number="B"),
        ],
        "sendingShipments": [],
    })
    client.async_fmp_delivery_window = AsyncMock(return_value=window)

    coordinator = DpdCoordinator(hass, client, _mock_entry())
    result = await coordinator._async_update_data()

    client.async_fmp_delivery_window.assert_awaited_once_with("hashA")
    # FMP window lives under the preserved `raw` payload after normalisation.
    assert result["incoming_active"][0]["raw"]["fmpDeliveryDateAndTime"] == window
    assert "fmpDeliveryDateAndTime" not in result["incoming_active"][1]["raw"]
