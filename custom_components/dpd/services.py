"""Actions for the DPD integration.

``dpd.track_parcel`` puts a parcel on the myDPD account by its number, the
same as adding it in the myDPD app, so the integration then follows it like
every other parcel on the account.

Only the general transport (the myDPD app account, ``countries/general``) can
add a parcel; the others are refused rather than pretended.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN, DpdApiError, DpdAuthError, DpdParcelNotFoundError

if TYPE_CHECKING:
    from . import DpdConfigEntry

SERVICE_TRACK_PARCEL = "track_parcel"
ATTR_TRACKING_CODE = "tracking_code"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

# 14 digits, once the spaces DPD prints it with are dropped.
_PARCEL_NUMBER = re.compile(r"^\d{14}$")

_TRACK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TRACKING_CODE): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    }
)


def _entry(hass: HomeAssistant, entry_id: str | None) -> DpdConfigEntry:
    """The DPD account to act on: the one named, or the only one loaded."""
    loaded = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if not loaded:
        raise ServiceValidationError("No DPD account is loaded")
    if entry_id is not None:
        loaded = [entry for entry in loaded if entry.entry_id == entry_id]
    if len(loaded) != 1:
        raise ServiceValidationError("Pass the config_entry_id of a loaded DPD account")
    return loaded[0]


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the DPD actions; called once from ``async_setup``."""

    async def _track(call: ServiceCall) -> None:
        number = re.sub(r"\s+", "", call.data[ATTR_TRACKING_CODE])
        if not _PARCEL_NUMBER.match(number):
            raise ServiceValidationError(
                f"{number} is not a DPD parcel number (14 digits)"
            )
        entry = _entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        client = entry.runtime_data.client
        if client is None:
            raise ServiceValidationError(
                "Adding a parcel by number only works for myDPD app accounts"
            )
        coordinator = entry.runtime_data.coordinator
        if coordinator.knows_parcel(number):
            return

        try:
            await client.async_add_parcel(number)
        except DpdParcelNotFoundError as err:
            raise ServiceValidationError(f"DPD does not know parcel {number}") from err
        except (DpdAuthError, DpdApiError, aiohttp.ClientError) as err:
            raise HomeAssistantError(f"Could not reach DPD: {err}") from err

        # DPD answering is not proof: only the account's own list is.
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            raise HomeAssistantError("Could not reach DPD to confirm the parcel")
        if not coordinator.knows_parcel(number):
            raise HomeAssistantError(
                f"DPD looked up parcel {number} but did not add it to the account"
            )

    hass.services.async_register(
        DOMAIN, SERVICE_TRACK_PARCEL, _track, schema=_TRACK_SCHEMA
    )
