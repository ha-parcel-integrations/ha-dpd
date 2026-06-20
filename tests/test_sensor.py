"""Tests for DPD sensor property logic."""
from datetime import datetime
from unittest.mock import MagicMock

from custom_components.dpd.sensor import (
    DpdDeliveredParcelsSensor,
    DpdEnRouteToParcelShopSensor,
    DpdIncomingParcelsSensor,
    DpdNextDeliverySensor,
    DpdOutgoingParcelsSensor,
    DpdParcelSensor,
)


def _make_coordinator(data: dict | None) -> MagicMock:
    coordinator = MagicMock()
    coordinator.data = data
    return coordinator


def _make_entry(entry_id: str = "test_entry", title: str = "user@example.com") -> MagicMock:
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.title = title
    return entry


def _shipment(
    parcel_number: str = "01XXXXXXXXXXXX",
    description: str = "ORDER_CREATED",
    sender: str = "Test Sender",
    delivery_date: str | None = None,
    delivery_type: str = "HOME",
    tz_id: str = "Europe/Amsterdam",
) -> dict:
    return {
        "parcelNumber": parcel_number,
        "shipmentId": parcel_number,
        "senderName": sender,
        "status": {
            "description": description,
            "deliveryType": delivery_type,
            "eventDateAndTimeZoneId": tz_id,
        },
        "deliveryDate": delivery_date,
    }


# ---------------------------------------------------------------------------
# DpdIncomingParcelsSensor
# ---------------------------------------------------------------------------


def test_incoming_sensor_counts_active_parcels():
    coordinator = _make_coordinator({
        "incoming_active": [_shipment("A"), _shipment("B")],
        "incoming_delivered": [],
        "outgoing_active": [],
    })
    sensor = DpdIncomingParcelsSensor(coordinator, _make_entry(), lambda _: None)
    assert sensor.native_value == 2


def test_incoming_sensor_zero_when_no_data():
    sensor = DpdIncomingParcelsSensor(_make_coordinator(None), _make_entry(), lambda _: None)
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"parcels": []}


def test_incoming_sensor_unique_id_uses_entry_id():
    sensor = DpdIncomingParcelsSensor(
        _make_coordinator(None), _make_entry(entry_id="abc"), lambda _: None
    )
    assert sensor.unique_id == "abc_incoming_parcels"


# ---------------------------------------------------------------------------
# DpdParcelSensor
# ---------------------------------------------------------------------------


def test_parcel_sensor_returns_status_description():
    parcel = _shipment("X1", description="OUT_FOR_DELIVERY")
    coordinator = _make_coordinator({"incoming_active": [parcel], "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdParcelSensor(coordinator, _make_entry(), "X1")
    assert sensor.native_value == "OUT_FOR_DELIVERY"


def test_parcel_sensor_returns_none_when_missing():
    coordinator = _make_coordinator({"incoming_active": [_shipment("A")], "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdParcelSensor(coordinator, _make_entry(), "MISSING")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_parcel_sensor_attributes_contain_full_parcel():
    parcel = _shipment("A")
    coordinator = _make_coordinator({"incoming_active": [parcel], "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdParcelSensor(coordinator, _make_entry(), "A")
    assert sensor.extra_state_attributes == parcel


def test_parcel_sensor_unique_id_is_namespaced():
    sensor = DpdParcelSensor(_make_coordinator(None), _make_entry(entry_id="abc"), "P1")
    assert sensor.unique_id == "abc_P1"
    assert sensor.name == "DPD Parcel P1"


# ---------------------------------------------------------------------------
# DpdOutgoingParcelsSensor
# ---------------------------------------------------------------------------


def test_outgoing_sensor_counts_active_shipments():
    coordinator = _make_coordinator({
        "incoming_active": [],
        "incoming_delivered": [],
        "outgoing_active": [_shipment("X"), _shipment("Y")],
    })
    sensor = DpdOutgoingParcelsSensor(coordinator, _make_entry())
    assert sensor.native_value == 2


def test_outgoing_sensor_zero_when_no_data():
    sensor = DpdOutgoingParcelsSensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"shipments": []}


# ---------------------------------------------------------------------------
# DpdDeliveredParcelsSensor
# ---------------------------------------------------------------------------


def test_delivered_sensor_count_matches_coordinator():
    coordinator = _make_coordinator({
        "incoming_active": [],
        "incoming_delivered": [_shipment("A", "DELIVERED"), _shipment("B", "DELIVERED")],
        "outgoing_active": [],
    })
    sensor = DpdDeliveredParcelsSensor(coordinator, _make_entry())
    assert sensor.native_value == 2


def test_delivered_sensor_zero_when_no_data():
    sensor = DpdDeliveredParcelsSensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"parcels": []}


def test_delivered_sensor_attributes_summarise_parcels():
    parcel = _shipment("A", "DELIVERED", sender="Ha-Ra GmbH", delivery_date="2026-06-05")
    parcel["plannedDeliveryFrom"] = "2026-06-05T00:00:00+02:00"
    parcel["plannedDeliveryTo"] = "2026-06-05T23:59:59+02:00"
    coordinator = _make_coordinator({
        "incoming_active": [],
        "incoming_delivered": [parcel],
        "outgoing_active": [],
    })
    sensor = DpdDeliveredParcelsSensor(coordinator, _make_entry())
    attrs = sensor.extra_state_attributes
    assert attrs == {
        "parcels": [
            {
                "parcelNumber": "A",
                "sender": "Ha-Ra GmbH",
                "status": "DELIVERED",
                "delivery_date": "2026-06-05",
                "plannedDeliveryFrom": "2026-06-05T00:00:00+02:00",
                "plannedDeliveryTo": "2026-06-05T23:59:59+02:00",
            }
        ]
    }


# ---------------------------------------------------------------------------
# DpdNextDeliverySensor
# ---------------------------------------------------------------------------


def test_next_delivery_picks_earliest_planned_date():
    parcels = [
        _shipment(parcel_number="A", delivery_date="2026-06-20"),
        _shipment(parcel_number="B", delivery_date="2026-06-17"),
        _shipment(parcel_number="C", delivery_date="2026-06-19"),
    ]
    coordinator = _make_coordinator({
        "incoming_active": parcels,
        "incoming_delivered": [],
        "outgoing_active": [],
    })
    sensor = DpdNextDeliverySensor(coordinator, _make_entry())
    result = sensor.native_value
    assert result is not None
    assert (result.year, result.month, result.day) == (2026, 6, 17)


def test_next_delivery_attributes_describe_earliest_parcel():
    parcels = [
        _shipment(parcel_number="A", sender="Sender A", delivery_date="2026-06-17"),
        _shipment(parcel_number="B", sender="Sender B", delivery_date="2026-06-20"),
    ]
    coordinator = _make_coordinator({"incoming_active": parcels, "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdNextDeliverySensor(coordinator, _make_entry())
    attrs = sensor.extra_state_attributes
    assert attrs["barcode"] == "A"
    assert attrs["sender"] == "Sender A"


def test_next_delivery_skips_parcels_without_date():
    parcels = [
        _shipment(parcel_number="A", delivery_date=None),
        _shipment(parcel_number="B", delivery_date="2026-06-17"),
    ]
    coordinator = _make_coordinator({"incoming_active": parcels, "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdNextDeliverySensor(coordinator, _make_entry())
    assert sensor.native_value is not None
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_none_when_no_parcels():
    sensor = DpdNextDeliverySensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_none_when_no_dates():
    parcels = [_shipment(parcel_number="A", delivery_date=None)]
    coordinator = _make_coordinator({"incoming_active": parcels, "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdNextDeliverySensor(coordinator, _make_entry())
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# DpdEnRouteToParcelShopSensor
# ---------------------------------------------------------------------------


def test_en_route_counts_parcelshop_parcels():
    parcels = [
        _shipment(parcel_number="A", delivery_type="PARCELSHOP"),
        _shipment(parcel_number="B", delivery_type="HOME"),
        _shipment(parcel_number="C", delivery_type="PARCELSHOP"),
    ]
    coordinator = _make_coordinator({"incoming_active": parcels, "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdEnRouteToParcelShopSensor(coordinator, _make_entry())
    assert sensor.native_value == 2


def test_en_route_excludes_home_delivery():
    parcels = [_shipment(parcel_number="A", delivery_type="HOME")]
    coordinator = _make_coordinator({"incoming_active": parcels, "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdEnRouteToParcelShopSensor(coordinator, _make_entry())
    assert sensor.native_value == 0


def test_en_route_zero_when_no_parcels():
    sensor = DpdEnRouteToParcelShopSensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value == 0


def test_en_route_attribute_lists_parcels():
    parcel = _shipment(parcel_number="A", delivery_type="PARCELSHOP")
    coordinator = _make_coordinator({"incoming_active": [parcel], "incoming_delivered": [], "outgoing_active": []})
    sensor = DpdEnRouteToParcelShopSensor(coordinator, _make_entry())
    assert sensor.extra_state_attributes == {"parcels": [parcel]}
