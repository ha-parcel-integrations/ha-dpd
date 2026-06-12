"""Tests for the DPD coordinator filter functions, data shape and error handling."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dpd.api import DpdApiError, DpdAuthError
from custom_components.dpd.const import (
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
)
from custom_components.dpd.coordinator import (
    DpdCoordinator,
    filter_active_shipments,
    filter_delivered_shipments,
    shipment_delivery_dt,
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
) -> dict:
    status: dict = {"description": description}
    if event_dt is not None:
        status["eventDateAndTime"] = event_dt
    if tz_id is not None:
        status["eventDateAndTimeZoneId"] = tz_id
    out = {"parcelNumber": parcel_number, "status": status}
    if delivery_date is not None:
        out["deliveryDate"] = delivery_date
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

    assert [s["parcelNumber"] for s in result["incoming_active"]] == ["A"]
    assert [s["parcelNumber"] for s in result["incoming_delivered"]] == ["B"]
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
