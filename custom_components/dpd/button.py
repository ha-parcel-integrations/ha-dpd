"""Button platform for the DPD integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.device_registry import DeviceEntryType
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import DpdConfigEntry
from .const import DOMAIN

# A manual refresh is a single API round-trip; HA's per-entity throttling
# adds nothing here.
PARALLEL_UPDATES = 0


def _build_device_info(entry: ConfigEntry) -> DeviceInfo:
    """Return the DeviceInfo shared with this account's sensors.

    Mirrors ``sensor._build_device_info`` so the button lands on the same
    ``DPD (<email>)`` device rather than spawning a second one.
    """
    email = entry.title or ""
    device_name = f"DPD ({email})" if email else "DPD"
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=device_name,
        manufacturer="DPD",
        entry_type=DeviceEntryType.SERVICE,
        configuration_url="https://www.dpdgroup.com",
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: DpdConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the DPD refresh button from a config entry."""
    async_add_entities([DpdRefreshButton(entry)])


class DpdRefreshButton(ButtonEntity):
    """Button that forces an immediate poll of DPD.

    Useful when a parcel is expected and the user does not want to wait for
    the next scheduled refresh. Stateless from HA's perspective.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "refresh"
    _attr_attribution = "Data provided by DPD"

    def __init__(self, entry: DpdConfigEntry) -> None:
        """Initialise the refresh button."""
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_refresh"
        self._attr_device_info = _build_device_info(entry)

    async def async_press(self) -> None:
        """Trigger an immediate refresh of the DPD coordinator."""
        await self._entry.runtime_data.coordinator.async_request_refresh()
