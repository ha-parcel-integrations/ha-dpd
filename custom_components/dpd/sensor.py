"""Sensor platform for the DPD integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DpdCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DPD sensor entities from a config entry."""
    coordinator: DpdCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    await coordinator.async_config_entry_first_refresh()

    # TODO: add per-parcel sensors, next-delivery sensor, en-route/pickup sensors,
    # and a delivered sensor once the parcel object shape is known.
    # See the DHL component for the patterns to mirror.
    async_add_entities(
        [
            DpdIncomingParcelsSensor(coordinator, entry),
            DpdOutgoingParcelsSensor(coordinator, entry),
        ]
    )


def _build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return a DeviceInfo dict shared by all sensors for this account."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="DPD",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://www.dpdgroup.com",
    )


class DpdIncomingParcelsSensor(CoordinatorEntity[DpdCoordinator], SensorEntity):
    """Summary sensor reporting the count of incoming DPD parcels."""

    _attr_name = "DPD Incoming Parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DpdCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_incoming_parcels"
        self._attr_device_info = _build_device_info(entry)

    @property
    def _parcels(self) -> list[dict]:
        # TODO: filter to active (non-delivered) once parcel categories are known.
        return (self.coordinator.data or {}).get("incoming", [])

    @property
    def native_value(self) -> int:
        return len(self._parcels)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"parcels": self._parcels}


class DpdOutgoingParcelsSensor(CoordinatorEntity[DpdCoordinator], SensorEntity):
    """Summary sensor reporting the count of outgoing DPD shipments."""

    _attr_name = "DPD Outgoing Parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DpdCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_outgoing_parcels"
        self._attr_device_info = _build_device_info(entry)

    @property
    def _shipments(self) -> list[dict]:
        # TODO: filter to active (non-delivered) once shipment categories are known.
        return (self.coordinator.data or {}).get("outgoing", [])

    @property
    def native_value(self) -> int:
        return len(self._shipments)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"shipments": self._shipments}
