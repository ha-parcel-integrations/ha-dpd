"""Actions for the DPD integration.

``dpd.track_parcel`` puts a parcel on the myDPD account by its number, the
same as adding it in the myDPD app, so the integration then follows it like
every other parcel on the account.

Only the general transport (the myDPD app account, ``countries/general``) can
add a parcel; the others are refused rather than pretended.

``dpd.update_contents`` sets or clears the text describing what is in a parcel,
for any account. With several accounts it goes to the account(s) that list the
code, or to the one named by ``config_entry_id``.
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

from .const import (
    CONF_CONTENTS,
    DOMAIN,
    DpdApiError,
    DpdAuthError,
    DpdParcelNotFoundError,
)
from .contents import normalize_code

if TYPE_CHECKING:
    from . import DpdConfigEntry

SERVICE_TRACK_PARCEL = "track_parcel"
SERVICE_UPDATE_CONTENTS = "update_contents"
ATTR_TRACKING_CODE = "tracking_code"
ATTR_CONFIG_ENTRY_ID = "config_entry_id"

# 14 digits, once the spaces DPD prints it with are dropped.
_PARCEL_NUMBER = re.compile(r"^\d{14}$")

_TRACK_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TRACKING_CODE): cv.string,
        vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
        vol.Optional(CONF_CONTENTS): cv.string,
    }
)
_UPDATE_CONTENTS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_TRACKING_CODE): cv.string,
        vol.Optional(CONF_CONTENTS): cv.string,
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
            # Already on the account: only a changed contents value does anything.
            if CONF_CONTENTS in call.data and coordinator.contents_store.async_set(
                number, call.data[CONF_CONTENTS]
            ):
                await coordinator.async_request_refresh()
            return

        try:
            await client.async_add_parcel(number)
        except DpdParcelNotFoundError as err:
            raise ServiceValidationError(f"DPD does not know parcel {number}") from err
        except (DpdAuthError, DpdApiError, aiohttp.ClientError) as err:
            raise HomeAssistantError(f"Could not reach DPD: {err}") from err

        if CONF_CONTENTS in call.data:
            # Stored before the refresh, which puts it on the parcel.
            coordinator.contents_store.async_set(number, call.data[CONF_CONTENTS])

        # DPD answering is not proof: only the account's own list is.
        await coordinator.async_refresh()
        if not coordinator.last_update_success:
            raise HomeAssistantError("Could not reach DPD to confirm the parcel")
        if not coordinator.knows_parcel(number):
            raise HomeAssistantError(
                f"DPD looked up parcel {number} but did not add it to the account"
            )

    async def _update_contents(call: ServiceCall) -> None:
        code = normalize_code(call.data[ATTR_TRACKING_CODE])
        if not code:
            raise ServiceValidationError("A tracking code is required")
        loaded = [
            entry
            for entry in hass.config_entries.async_entries(DOMAIN)
            if entry.state is ConfigEntryState.LOADED
        ]
        if not loaded:
            raise ServiceValidationError("No DPD account is loaded")
        if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
            targets = [entry for entry in loaded if entry.entry_id == entry_id]
            if not targets:
                raise ServiceValidationError(
                    f"'{entry_id}' is not a loaded DPD account"
                )
        elif len(loaded) == 1:
            targets = loaded
        else:
            # A code can sit in two accounts, so every account that lists it
            # gets the value; guessing an account would write to the wrong one.
            targets = [
                entry
                for entry in loaded
                if code in entry.runtime_data.coordinator.known_codes
            ]
            if not targets:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="update_contents_entry_required",
                )
        for entry in targets:
            coordinator = entry.runtime_data.coordinator
            if coordinator.contents_store.async_set(code, call.data.get(CONF_CONTENTS)):
                await coordinator.async_request_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_TRACK_PARCEL, _track, schema=_TRACK_SCHEMA
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_UPDATE_CONTENTS,
        _update_contents,
        schema=_UPDATE_CONTENTS_SCHEMA,
    )
