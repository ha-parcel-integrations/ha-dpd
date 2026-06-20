"""Tests for DPD sensor property logic.

The sensors read from ``coordinator.data``, which after the 1.x → 2.0
normalisation now carries carrier-agnostic parcel dicts (``barcode``,
``status`` enum, ``planned_from`` / ``planned_to`` ISO strings,
``pickup`` bool, etc.). The ``_parcel`` helper here builds that
shape directly — bypassing the coordinator's raw-to-normalised
transformation tested in ``test_coordinator.py``.
"""
from datetime import datetime
from unittest.mock import MagicMock

from custom_components.dpd.const import ParcelStatus
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


def _parcel(
    barcode: str = "01XXXXXXXXXXXX",
    status: ParcelStatus = ParcelStatus.REGISTERED,
    sender: str = "Test Sender",
    pickup: bool = False,
    planned_from: str | None = None,
    planned_to: str | None = None,
    delivered: bool = False,
    delivered_at: str | None = None,
    raw: dict | None = None,
) -> dict:
    return {
        "carrier": "DPD",
        "barcode": barcode,
        "sender": sender,
        "status": status,
        "raw_status": "ORDER_CREATED",
        "delivered": delivered,
        "delivered_at": delivered_at,
        "planned_from": planned_from,
        "planned_to": planned_to,
        "pickup": pickup,
        "pickup_point": None,
        "url": f"https://www.dpdgroup.com/nl/mydpd/my-parcels/search?parcelNumber={barcode}",
        "raw": raw if raw is not None else {"_": "raw payload"},
    }


# ---------------------------------------------------------------------------
# DpdIncomingParcelsSensor
# ---------------------------------------------------------------------------


def test_incoming_sensor_counts_active_parcels():
    coordinator = _make_coordinator({
        "incoming_active": [_parcel("A"), _parcel("B")],
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


def test_parcel_sensor_returns_parcel_status_enum_value():
    parcel = _parcel("X1", status=ParcelStatus.OUT_FOR_DELIVERY)
    coordinator = _make_coordinator({
        "incoming_active": [parcel],
        "incoming_delivered": [],
        "outgoing_active": [],
    })
    sensor = DpdParcelSensor(coordinator, _make_entry(), "X1")
    assert sensor.native_value == ParcelStatus.OUT_FOR_DELIVERY
    assert sensor.native_value == "out_for_delivery"


def test_parcel_sensor_returns_none_when_missing():
    coordinator = _make_coordinator({
        "incoming_active": [_parcel("A")],
        "incoming_delivered": [],
        "outgoing_active": [],
    })
    sensor = DpdParcelSensor(coordinator, _make_entry(), "MISSING")
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_parcel_sensor_attributes_contain_full_parcel():
    parcel = _parcel("A")
    coordinator = _make_coordinator({
        "incoming_active": [parcel],
        "incoming_delivered": [],
        "outgoing_active": [],
    })
    sensor = DpdParcelSensor(coordinator, _make_entry(), "A")
    assert sensor.extra_state_attributes == parcel


def test_parcel_sensor_unique_id_is_namespaced():
    sensor = DpdParcelSensor(_make_coordinator(None), _make_entry(entry_id="abc"), "P1")
    assert sensor.unique_id == "abc_P1"
    # Friendly name is rendered by HA from `parcel` translation key plus the
    # barcode placeholder; the sensor object exposes the contract pieces.
    assert sensor.translation_key == "parcel"
    assert sensor.translation_placeholders == {"barcode": "P1"}


# ---------------------------------------------------------------------------
# DpdOutgoingParcelsSensor
# ---------------------------------------------------------------------------


def test_outgoing_sensor_counts_active_shipments():
    coordinator = _make_coordinator({
        "incoming_active": [],
        "incoming_delivered": [],
        "outgoing_active": [_parcel("X"), _parcel("Y")],
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
        "incoming_delivered": [
            _parcel("A", status=ParcelStatus.DELIVERED, delivered=True),
            _parcel("B", status=ParcelStatus.DELIVERED, delivered=True),
        ],
        "outgoing_active": [],
    })
    sensor = DpdDeliveredParcelsSensor(coordinator, _make_entry())
    assert sensor.native_value == 2


def test_delivered_sensor_zero_when_no_data():
    sensor = DpdDeliveredParcelsSensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"parcels": []}


def test_delivered_sensor_attributes_pass_through_normalized_parcels():
    parcel = _parcel(
        "A",
        status=ParcelStatus.DELIVERED,
        sender="Ha-Ra GmbH",
        delivered=True,
        delivered_at="2026-06-05T14:23:12+02:00",
    )
    coordinator = _make_coordinator({
        "incoming_active": [],
        "incoming_delivered": [parcel],
        "outgoing_active": [],
    })
    sensor = DpdDeliveredParcelsSensor(coordinator, _make_entry())
    assert sensor.extra_state_attributes == {"parcels": [parcel]}


# ---------------------------------------------------------------------------
# DpdNextDeliverySensor
# ---------------------------------------------------------------------------


def test_next_delivery_picks_earliest_planned_from():
    parcels = [
        _parcel("A", planned_from="2026-06-20T10:00:00+02:00"),
        _parcel("B", planned_from="2026-06-17T08:00:00+02:00"),
        _parcel("C", planned_from="2026-06-19T15:00:00+02:00"),
    ]
    coordinator = _make_coordinator({
        "incoming_active": parcels,
        "incoming_delivered": [],
        "outgoing_active": [],
    })
    sensor = DpdNextDeliverySensor(coordinator, _make_entry())
    result = sensor.native_value
    assert result is not None
    assert (result.year, result.month, result.day, result.hour) == (2026, 6, 17, 8)


def test_next_delivery_attributes_describe_earliest_parcel():
    parcels = [
        _parcel("A", sender="Sender A", planned_from="2026-06-17T10:00:00+02:00"),
        _parcel("B", sender="Sender B", planned_from="2026-06-20T10:00:00+02:00"),
    ]
    coordinator = _make_coordinator({
        "incoming_active": parcels, "incoming_delivered": [], "outgoing_active": [],
    })
    sensor = DpdNextDeliverySensor(coordinator, _make_entry())
    attrs = sensor.extra_state_attributes
    assert attrs["barcode"] == "A"
    assert attrs["sender"] == "Sender A"


def test_next_delivery_skips_parcels_without_planned_from():
    parcels = [
        _parcel("A", planned_from=None),
        _parcel("B", planned_from="2026-06-17T10:00:00+02:00"),
    ]
    coordinator = _make_coordinator({
        "incoming_active": parcels, "incoming_delivered": [], "outgoing_active": [],
    })
    sensor = DpdNextDeliverySensor(coordinator, _make_entry())
    assert sensor.native_value is not None
    assert sensor.extra_state_attributes["barcode"] == "B"


def test_next_delivery_none_when_no_parcels():
    sensor = DpdNextDeliverySensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value is None
    assert sensor.extra_state_attributes == {}


def test_next_delivery_none_when_no_planned_from():
    parcels = [_parcel("A", planned_from=None)]
    coordinator = _make_coordinator({
        "incoming_active": parcels, "incoming_delivered": [], "outgoing_active": [],
    })
    sensor = DpdNextDeliverySensor(coordinator, _make_entry())
    assert sensor.native_value is None


# ---------------------------------------------------------------------------
# DpdEnRouteToParcelShopSensor
# ---------------------------------------------------------------------------


def test_en_route_counts_pickup_parcels():
    parcels = [
        _parcel("A", pickup=True),
        _parcel("B", pickup=False),
        _parcel("C", pickup=True),
    ]
    coordinator = _make_coordinator({
        "incoming_active": parcels, "incoming_delivered": [], "outgoing_active": [],
    })
    sensor = DpdEnRouteToParcelShopSensor(coordinator, _make_entry())
    assert sensor.native_value == 2


def test_en_route_excludes_home_delivery():
    parcels = [_parcel("A", pickup=False)]
    coordinator = _make_coordinator({
        "incoming_active": parcels, "incoming_delivered": [], "outgoing_active": [],
    })
    sensor = DpdEnRouteToParcelShopSensor(coordinator, _make_entry())
    assert sensor.native_value == 0


def test_en_route_zero_when_no_parcels():
    sensor = DpdEnRouteToParcelShopSensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value == 0


def test_en_route_attribute_lists_parcels():
    parcel = _parcel("A", pickup=True)
    coordinator = _make_coordinator({
        "incoming_active": [parcel], "incoming_delivered": [], "outgoing_active": [],
    })
    sensor = DpdEnRouteToParcelShopSensor(coordinator, _make_entry())
    assert sensor.extra_state_attributes == {"parcels": [parcel]}
