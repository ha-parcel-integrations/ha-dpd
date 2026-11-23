"""Tests for the DPD deliveries calendar."""

from datetime import UTC, date, datetime
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
    cal = _calendar(
        [
            _parcel("LATE", planned_from="2099-01-02T10:00:00Z"),
            _parcel("SOON", planned_from="2099-01-01T10:00:00Z"),
        ]
    )
    event = cal.event
    assert event is not None
    assert event.uid == "SOON"
    assert event.summary == "Example Sender"


def test_contents_is_the_event_title():
    parcel = _parcel("A", planned_from="2099-01-01T10:00:00Z")
    parcel["contents"] = "Garden hose"
    assert _calendar([parcel])._events()[0].summary == "Garden hose"


def test_title_falls_back_to_sender_without_contents():
    parcel = _parcel("A", planned_from="2099-01-01T10:00:00Z")
    parcel["contents"] = None
    assert _calendar([parcel])._events()[0].summary == "Example Sender"


def test_title_keeps_the_old_fallback_without_contents_or_sender():
    parcel = _parcel("A", planned_from="2099-01-01T10:00:00Z")
    parcel["sender"] = None
    assert _calendar([parcel])._events()[0].summary == "Parcel A"


def test_event_none_when_no_planned_parcels():
    cal = _calendar([_parcel("NOPLAN")])
    assert cal.event is None


def test_moment_gets_one_hour_duration():
    cal = _calendar([_parcel("A", planned_from="2099-01-01T10:00:00Z")])
    events = cal._events()
    assert len(events) == 1
    assert events[0].end == datetime(2099, 1, 1, 11, 0, tzinfo=UTC)


def test_interval_uses_window():
    cal = _calendar(
        [
            _parcel(
                "A",
                planned_from="2099-01-01T10:00:00Z",
                planned_to="2099-01-01T12:00:00Z",
            )
        ]
    )
    assert cal._events()[0].end == datetime(2099, 1, 1, 12, 0, tzinfo=UTC)


def test_pickup_parcel_sets_location():
    cal = _calendar(
        [
            _parcel(
                "A",
                planned_from="2099-01-01T10:00:00Z",
                pickup=True,
                pickup_point="DPD ParcelShop",
            )
        ]
    )
    assert cal._events()[0].location == "DPD ParcelShop"


async def test_get_events_filters_by_range():
    cal = _calendar(
        [
            _parcel("PAST", planned_from="2000-01-01T10:00:00Z"),
            _parcel("FUTURE", planned_from="2099-01-01T10:00:00Z"),
        ]
    )
    start = datetime(2098, 1, 1, tzinfo=UTC)
    end = datetime(2100, 1, 1, tzinfo=UTC)
    events = await cal.async_get_events(MagicMock(), start, end)
    assert {e.uid for e in events} == {"FUTURE"}


def test_whole_day_window_is_an_all_day_event():
    cal = _calendar(
        [
            _parcel(
                "A",
                planned_from="2099-01-01T00:00:00+01:00",
                planned_to="2099-01-01T23:59:59+01:00",
            )
        ]
    )
    event = cal._events()[0]
    assert (event.start, event.end) == (date(2099, 1, 1), date(2099, 1, 2))
    assert event.all_day


def test_window_over_whole_days_is_one_all_day_event():
    cal = _calendar(
        [
            _parcel(
                "A",
                planned_from="2099-01-01T00:00:00",
                planned_to="2099-01-03T23:59:59",
            )
        ]
    )
    event = cal._events()[0]
    assert (event.start, event.end) == (date(2099, 1, 1), date(2099, 1, 4))


async def test_whole_day_window_sent_in_utc_is_all_day_in_local_time(hass):
    await hass.config.async_set_time_zone("Europe/Amsterdam")
    cal = _calendar(
        [
            _parcel(
                "A",
                planned_from="2099-01-01T23:00:00Z",
                planned_to="2099-01-02T22:59:59Z",
            )
        ]
    )
    event = cal._events()[0]
    assert (event.start, event.end) == (date(2099, 1, 2), date(2099, 1, 3))


def test_a_moment_stretched_to_the_end_of_its_day_stays_timed():
    cal = _calendar(
        [
            _parcel(
                "A",
                planned_from="2099-01-01T14:00:00+01:00",
                planned_to="2099-01-01T23:59:59+01:00",
            )
        ]
    )
    assert not cal._events()[0].all_day


async def test_all_day_events_are_upcoming_and_filtered_by_range():
    cal = _calendar(
        [
            _parcel(
                "PAST",
                planned_from="2000-01-01T00:00:00",
                planned_to="2000-01-01T23:59:59",
            ),
            _parcel(
                "FUTURE",
                planned_from="2099-01-01T00:00:00",
                planned_to="2099-01-01T23:59:59",
            ),
        ]
    )
    assert cal.event.uid == "FUTURE"
    start = datetime(2098, 1, 1, tzinfo=UTC)
    end = datetime(2100, 1, 1, tzinfo=UTC)
    events = await cal.async_get_events(MagicMock(), start, end)
    assert {e.uid for e in events} == {"FUTURE"}
