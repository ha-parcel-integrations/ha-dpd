"""Sensor platform for the DPD integration."""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DpdCoordinator, shipment_planned_dt

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up DPD sensor entities from a config entry."""
    coordinator: DpdCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    await coordinator.async_config_entry_first_refresh()

    current_parcels: list[dict] = (coordinator.data or {}).get("incoming_active", [])
    current_numbers: set[str] = {
        p.get("parcelNumber", "") for p in current_parcels if p.get("parcelNumber")
    }

    # Drop per-parcel entries from the registry that are no longer active —
    # handles parcels that were delivered between HA restarts.
    registry = er.async_get(hass)
    entry_id = entry.entry_id
    non_parcel_unique_ids = {
        f"{entry_id}_incoming_parcels",
        f"{entry_id}_outgoing_parcels",
        f"{entry_id}_delivered_parcels",
        f"{entry_id}_next_delivery",
        f"{entry_id}_en_route_to_parcel_shop",
    }
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            entity_entry.unique_id.startswith(f"{entry_id}_")
            and entity_entry.unique_id not in non_parcel_unique_ids
        ):
            parcel_number = entity_entry.unique_id[len(f"{entry_id}_"):]
            if parcel_number not in current_numbers:
                registry.async_remove(entity_entry.entity_id)

    entities: list[SensorEntity] = [
        DpdIncomingParcelsSensor(
            coordinator, entry, async_add_entities, current_numbers
        ),
        DpdOutgoingParcelsSensor(coordinator, entry),
        DpdDeliveredParcelsSensor(coordinator, entry),
        DpdNextDeliverySensor(coordinator, entry),
        DpdEnRouteToParcelShopSensor(coordinator, entry),
    ]
    for parcel in current_parcels:
        parcel_number = parcel.get("parcelNumber", "")
        if parcel_number:
            entities.append(DpdParcelSensor(coordinator, entry, parcel_number))

    async_add_entities(entities)


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
    """Summary sensor reporting the count of active incoming DPD parcels.

    Also manages the lifecycle of per-parcel :class:`DpdParcelSensor` entities:
    new parcel numbers are added and stale ones are removed from the entity
    registry whenever the coordinator data changes.
    """

    _attr_name = "DPD Incoming Parcels"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self,
        coordinator: DpdCoordinator,
        entry: ConfigEntry,
        async_add_entities: AddEntitiesCallback,
        known_parcel_numbers: set[str] | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._async_add_entities = async_add_entities
        self._known_parcel_numbers: set[str] = known_parcel_numbers or set()
        self._attr_unique_id = f"{entry.entry_id}_incoming_parcels"
        self._attr_device_info = _build_device_info(entry)

    @property
    def _parcels(self) -> list[dict]:
        return (self.coordinator.data or {}).get("incoming_active", [])

    @property
    def native_value(self) -> int:
        return len(self._parcels)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"parcels": self._parcels}

    def _handle_coordinator_update(self) -> None:
        current_parcels = self._parcels
        current_numbers = {
            p.get("parcelNumber", "")
            for p in current_parcels
            if p.get("parcelNumber")
        }

        new_numbers = current_numbers - self._known_parcel_numbers
        if new_numbers:
            self._async_add_entities(
                [
                    DpdParcelSensor(self.coordinator, self._entry, n)
                    for n in new_numbers
                ]
            )

        stale_numbers = self._known_parcel_numbers - current_numbers
        if stale_numbers and self.hass is not None:
            registry = er.async_get(self.hass)
            entry_id = self._entry.entry_id
            for number in stale_numbers:
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, f"{entry_id}_{number}"
                )
                if entity_id:
                    registry.async_remove(entity_id)

        self._known_parcel_numbers = current_numbers
        super()._handle_coordinator_update()


class DpdParcelSensor(CoordinatorEntity[DpdCoordinator], SensorEntity):
    """Per-parcel sensor reporting the status of a single active incoming shipment."""

    _attr_icon = "mdi:package-variant-closed"

    def __init__(
        self,
        coordinator: DpdCoordinator,
        entry: ConfigEntry,
        parcel_number: str,
    ) -> None:
        super().__init__(coordinator)
        self._parcel_number = parcel_number
        self._attr_unique_id = f"{entry.entry_id}_{parcel_number}"
        self._attr_name = f"DPD Parcel {parcel_number}"
        self._attr_device_info = _build_device_info(entry)

    def _get_parcel(self) -> dict[str, Any] | None:
        for parcel in (self.coordinator.data or {}).get("incoming_active", []):
            if parcel.get("parcelNumber") == self._parcel_number:
                return parcel
        return None

    @property
    def native_value(self) -> str | None:
        parcel = self._get_parcel()
        if not parcel:
            return None
        return (parcel.get("status") or {}).get("description")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        parcel = self._get_parcel()
        return dict(parcel) if parcel else {}


class DpdOutgoingParcelsSensor(CoordinatorEntity[DpdCoordinator], SensorEntity):
    """Summary sensor reporting the count of active outgoing DPD shipments."""

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
        return (self.coordinator.data or {}).get("outgoing_active", [])

    @property
    def native_value(self) -> int:
        return len(self._shipments)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"shipments": self._shipments}


class DpdDeliveredParcelsSensor(CoordinatorEntity[DpdCoordinator], SensorEntity):
    """Sensor reporting recently delivered incoming DPD parcels."""

    _attr_name = "DPD Delivered Parcels"
    _attr_icon = "mdi:package-variant"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DpdCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_delivered_parcels"
        self._attr_device_info = _build_device_info(entry)

    @property
    def _parcels(self) -> list[dict]:
        return (self.coordinator.data or {}).get("incoming_delivered", [])

    @property
    def native_value(self) -> int:
        return len(self._parcels)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "parcels": [
                {
                    "parcelNumber": p.get("parcelNumber"),
                    "sender": p.get("senderName"),
                    "status": (p.get("status") or {}).get("description"),
                    "delivery_date": p.get("deliveryDate"),
                }
                for p in self._parcels
            ]
        }


class DpdNextDeliverySensor(CoordinatorEntity[DpdCoordinator], SensorEntity):
    """Earliest expected delivery datetime across all active incoming DPD parcels.

    DPD only exposes a delivery **date** (no time) for active parcels, so the
    timestamp is midnight in the parcel's reported timezone — useful for
    "delivery is today/tomorrow" automations rather than precise hour windows.
    """

    _attr_name = "DPD Next Delivery"
    _attr_icon = "mdi:clock-fast"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: DpdCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_next_delivery"
        self._attr_device_info = _build_device_info(entry)

    def _delivery_moments(self) -> list[tuple[datetime, dict]]:
        result: list[tuple[datetime, dict]] = []
        for parcel in (self.coordinator.data or {}).get("incoming_active", []):
            dt = shipment_planned_dt(parcel)
            if dt is not None:
                result.append((dt, parcel))
        return result

    @property
    def native_value(self) -> datetime | None:
        moments = self._delivery_moments()
        return min(dt for dt, _ in moments) if moments else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        moments = self._delivery_moments()
        if not moments:
            return {}
        _, earliest = min(moments, key=lambda x: x[0])
        return {
            "barcode": earliest.get("parcelNumber"),
            "sender": earliest.get("senderName"),
        }


class DpdEnRouteToParcelShopSensor(CoordinatorEntity[DpdCoordinator], SensorEntity):
    """Active incoming DPD parcels destined for a ParcelShop pickup point.

    Counts all non-delivered parcels with ``status.deliveryType == "PARCELSHOP"``.
    DPD does not yet expose a distinct "arrived at ParcelShop" status, so this
    sensor cannot separate in-transit from awaiting-collection parcels — a
    separate awaiting-pickup sensor will be added once that status is mapped.
    """

    _attr_name = "DPD Parcels En Route to ParcelShop"
    _attr_icon = "mdi:truck-delivery"
    _attr_native_unit_of_measurement = "parcels"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: DpdCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_en_route_to_parcel_shop"
        self._attr_device_info = _build_device_info(entry)

    def _get_parcelshop_parcels(self) -> list[dict]:
        return [
            p for p in (self.coordinator.data or {}).get("incoming_active", [])
            if (p.get("status") or {}).get("deliveryType") == "PARCELSHOP"
        ]

    @property
    def native_value(self) -> int:
        return len(self._get_parcelshop_parcels())

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"parcels": self._get_parcelshop_parcels()}
