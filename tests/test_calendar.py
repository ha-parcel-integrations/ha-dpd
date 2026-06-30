"""Tests for the DPD deliveries calendar."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

from custom_components.dpd.calendar import DpdDeliveriesCalendar


def _make_coordinator(active: list[dict]) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = {"incoming_active": active}
    return coordinator


def _parcel(
    barcode: str,
    planned_from: str | None = None,
    planned_to: str | None = None,
    pickup: bool = False,
    pickup_point: str | None = None,
) -> dict:
    return {
        "barcode": barcode,
        "sender": "Example Sender",
        "status": "out_for_delivery",
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": pickup_point,
        "url": "https://track/123",
    }


def _calendar(active: list[dict]) -> DpdDeliveriesCalendar:
    entry = MagicMock()
    entry.entry_id = "abc"
    entry.title = "user@example.com"
    return DpdDeliveriesCalendar(_make_coordinator(active), entry)


def test_event_returns_earliest_upcoming():
    cal = _calendar([
        _parcel("LATE", planned_from="2099-01-02T10:00:00Z"),
        _parcel("SOON", planned_from="2099-01-01T10:00:00Z"),
    ])
    event = cal.event
    assert event is not None
    assert event.uid == "SOON"
    assert event.summary == "Example Sender"


def test_event_none_when_no_planned_parcels():
    cal = _calendar([_parcel("NOPLAN")])
    assert cal.event is None


def test_moment_gets_one_hour_duration():
    cal = _calendar([_parcel("A", planned_from="2099-01-01T10:00:00Z")])
    events = cal._events()
    assert len(events) == 1
    assert events[0].end == datetime(2099, 1, 1, 11, 0, tzinfo=timezone.utc)


def test_interval_uses_window():
    cal = _calendar([
        _parcel(
            "A",
            planned_from="2099-01-01T10:00:00Z",
            planned_to="2099-01-01T12:00:00Z",
        )
    ])
    assert cal._events()[0].end == datetime(2099, 1, 1, 12, 0, tzinfo=timezone.utc)


def test_pickup_parcel_sets_location():
    cal = _calendar([
        _parcel(
            "A",
            planned_from="2099-01-01T10:00:00Z",
            pickup=True,
            pickup_point="DPD ParcelShop",
        )
    ])
    assert cal._events()[0].location == "DPD ParcelShop"


async def test_get_events_filters_by_range():
    cal = _calendar([
        _parcel("PAST", planned_from="2000-01-01T10:00:00Z"),
        _parcel("FUTURE", planned_from="2099-01-01T10:00:00Z"),
    ])
    start = datetime(2098, 1, 1, tzinfo=timezone.utc)
    end = datetime(2100, 1, 1, tzinfo=timezone.utc)
    events = await cal.async_get_events(MagicMock(), start, end)
    assert {e.uid for e in events} == {"FUTURE"}
