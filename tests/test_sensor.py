"""Tests for DPD sensor property logic."""
from unittest.mock import MagicMock

from custom_components.dpd.sensor import (
    DpdIncomingParcelsSensor,
    DpdOutgoingParcelsSensor,
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


# ---------------------------------------------------------------------------
# DpdIncomingParcelsSensor
# ---------------------------------------------------------------------------


def test_incoming_sensor_counts_all_parcels():
    coordinator = _make_coordinator({"incoming": [{"id": "A"}, {"id": "B"}], "outgoing": []})
    sensor = DpdIncomingParcelsSensor(coordinator, _make_entry())
    assert sensor.native_value == 2


def test_incoming_sensor_exposes_parcels_attribute():
    parcels = [{"id": "A"}, {"id": "B"}]
    coordinator = _make_coordinator({"incoming": parcels, "outgoing": []})
    sensor = DpdIncomingParcelsSensor(coordinator, _make_entry())
    assert sensor.extra_state_attributes == {"parcels": parcels}


def test_incoming_sensor_zero_when_no_data():
    sensor = DpdIncomingParcelsSensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"parcels": []}


def test_incoming_sensor_zero_when_empty_list():
    coordinator = _make_coordinator({"incoming": [], "outgoing": []})
    sensor = DpdIncomingParcelsSensor(coordinator, _make_entry())
    assert sensor.native_value == 0


def test_incoming_sensor_unique_id_includes_entry_id():
    sensor = DpdIncomingParcelsSensor(_make_coordinator(None), _make_entry(entry_id="abc123"))
    assert sensor.unique_id == "abc123_incoming_parcels"


# ---------------------------------------------------------------------------
# DpdOutgoingParcelsSensor
# ---------------------------------------------------------------------------


def test_outgoing_sensor_counts_all_shipments():
    coordinator = _make_coordinator({"incoming": [], "outgoing": [{"id": "X"}, {"id": "Y"}, {"id": "Z"}]})
    sensor = DpdOutgoingParcelsSensor(coordinator, _make_entry())
    assert sensor.native_value == 3


def test_outgoing_sensor_exposes_shipments_attribute():
    shipments = [{"id": "X"}]
    coordinator = _make_coordinator({"incoming": [], "outgoing": shipments})
    sensor = DpdOutgoingParcelsSensor(coordinator, _make_entry())
    assert sensor.extra_state_attributes == {"shipments": shipments}


def test_outgoing_sensor_zero_when_no_data():
    sensor = DpdOutgoingParcelsSensor(_make_coordinator(None), _make_entry())
    assert sensor.native_value == 0
    assert sensor.extra_state_attributes == {"shipments": []}


def test_outgoing_sensor_unique_id_includes_entry_id():
    sensor = DpdOutgoingParcelsSensor(_make_coordinator(None), _make_entry(entry_id="abc123"))
    assert sensor.unique_id == "abc123_outgoing_parcels"
