"""Actions for the DPD integration.

``dpd.track_parcel`` puts a parcel on the myDPD account by its number, the
same as adding it in the myDPD app, so the integration then follows it like
every other parcel on the account. It saves opening the app for a parcel the
account did not pick up by itself, and lets an automation do it: a dashboard
button, or a script that reads the number from a shipping e-mail.

Only the general transport (the myDPD app account, ``countries/general``) can
add a parcel. The other transports have no equivalent that has been seen, so
the action refuses them with a clear message rather than pretending.
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

# A DPD parcel number is 14 digits. Spaces are dropped first, because that is
# how the number is printed in DPD's e-mails and on labels.
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
    if entry_id:
        for entry in loaded:
            if entry.entry_id == entry_id:
                return entry
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="entry_not_loaded",
            translation_placeholders={"entry_id": entry_id},
        )
    if len(loaded) == 1:
        return loaded[0]
    if not loaded:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_entry_loaded"
        )
    raise ServiceValidationError(
        translation_domain=DOMAIN, translation_key="pick_an_account"
    )


def _on_account(entry: DpdConfigEntry, number: str) -> bool:
    data = entry.runtime_data.coordinator.data or {}
    return any(
        parcel.get("barcode") == number
        for parcels in data.values()
        for parcel in parcels
    )


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the DPD actions; called once from ``async_setup``."""

    async def _track(call: ServiceCall) -> None:
        number = re.sub(r"\s+", "", call.data[ATTR_TRACKING_CODE])
        if not _PARCEL_NUMBER.match(number):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_parcel_number",
                translation_placeholders={"number": number},
            )
        entry = _entry(hass, call.data.get(ATTR_CONFIG_ENTRY_ID))
        client = entry.runtime_data.client
        if client is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="track_not_supported"
            )
        if _on_account(entry, number):
            return  # already followed: nothing to do

        try:
            await client.async_add_parcel(number)
        except DpdParcelNotFoundError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="parcel_not_found",
                translation_placeholders={"number": number},
            ) from err
        except (DpdAuthError, DpdApiError, aiohttp.ClientError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="dpd_unreachable",
                translation_placeholders={"error": str(err)},
            ) from err

        coordinator = entry.runtime_data.coordinator
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="dpd_unreachable",
                translation_placeholders={"error": "refresh failed"},
            )
        if not _on_account(entry, number):
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="parcel_not_added",
                translation_placeholders={"number": number},
            )

    hass.services.async_register(
        DOMAIN, SERVICE_TRACK_PARCEL, _track, schema=_TRACK_SCHEMA
    )
