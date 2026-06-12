"""Tests for DPD sensor property logic."""
from unittest.mock import MagicMock

from custom_components.dpd.sensor import (
    DpdDeliveredParcelsSensor,
    DpdIncomingParcelsSensor,
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
) -> dict:
    return {
        "parcelNumber": parcel_number,
        "shipmentId": parcel_number,
        "senderName": sender,
        "status": {"description": description},
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
            }
        ]
    }
