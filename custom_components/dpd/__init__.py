"""DPD custom component for Home Assistant."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import DpdApiClient, DpdApiError, DpdAuthError
from .const import (
    CONF_BU,
    CONF_COUNTRY,
    CONF_DE_HARDWARE_ID,
    CONF_REFRESH_TOKEN,
    COUNTRY_DE,
    COUNTRY_GENERAL,
    COUNTRY_PL,
    DEFAULT_BU,
    PLATFORMS,
)
from .coordinator import DpdCoordinator
from .countries.de.session import DpdDeSession
from .countries.pl.session import DpdPlSession

_LOGGER = logging.getLogger(__name__)


@dataclass
class DpdData:
    """Runtime data attached to a DPD config entry."""

    client: DpdApiClient | None
    coordinator: DpdCoordinator
    de_session: DpdDeSession | None = None
    pl_session: DpdPlSession | None = None


type DpdConfigEntry = ConfigEntry[DpdData]


async def async_setup_entry(hass: HomeAssistant, entry: DpdConfigEntry) -> bool:
    """Set up DPD from a config entry."""
    session = async_get_clientsession(hass)
    country = entry.data.get(CONF_COUNTRY, COUNTRY_GENERAL.upper())

    client: DpdApiClient | None = None
    de_session: DpdDeSession | None = None
    pl_session: DpdPlSession | None = None

    try:
        if country == COUNTRY_DE.upper():
            hardware_id = entry.data.get(CONF_DE_HARDWARE_ID) or str(uuid4())
            de_session = DpdDeSession(
                session,
                entry.data[CONF_EMAIL],
                entry.data[CONF_PASSWORD],
                hardware_id,
            )
            await de_session.async_login()
        elif country == COUNTRY_PL.upper():
            def _store_refresh_token(token: str) -> None:
                hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
                )
            pl_session = DpdPlSession(
                session, entry.data.get(CONF_REFRESH_TOKEN), _store_refresh_token
            )
            await pl_session.async_login()
        else:
            client = DpdApiClient(
                entry.data[CONF_EMAIL],
                entry.data[CONF_PASSWORD],
                session,
                bu=entry.data.get(CONF_BU, DEFAULT_BU),
            )
            await client.async_login()
    except DpdAuthError as exc:
        # Keep the cause in the integration log (without credentials) so an
        # issue can contain the useful provider-side failure category. HA's
        # ConfigEntryAuthFailed intentionally exposes only a generic message.
        _LOGGER.warning(
            "DPD authentication failed for country %s; reauthentication is "
            "required (%s)",
            country,
            exc,
        )
        raise ConfigEntryAuthFailed("DPD authentication failed") from exc
    except DpdApiError as exc:
        # Non-success HTTP status during the auth flow — almost always a 5xx
        # from DPD's auth tier. Surface it as a transient setup failure so HA
        # retries with backoff instead of pushing the user into reauth.
        raise ConfigEntryNotReady(
            f"DPD authentication service returned HTTP {exc.status_code}"
        ) from exc
    except aiohttp.ClientError as exc:
        raise ConfigEntryNotReady("Unable to connect to DPD") from exc

    coordinator = DpdCoordinator(hass, client, entry, de_session=de_session, pl_session=pl_session)

    # Fetch initial data here, before forwarding to platforms. Raising
    # ConfigEntryNotReady from a forwarded platform is too late for HA to catch
    # cleanly (it logs a warning and half-sets-up the entry); doing the first
    # refresh here lets a transient failure fail the whole entry so HA retries
    # it with backoff.
    await coordinator.async_config_entry_first_refresh()

    # The PL provider can rotate refresh tokens during the first poll.
    if pl_session and pl_session.refresh_token != entry.data.get(CONF_REFRESH_TOKEN):
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_REFRESH_TOKEN: pl_session.refresh_token})
    entry.runtime_data = DpdData(client=client, coordinator=coordinator, de_session=de_session, pl_session=pl_session)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DpdConfigEntry) -> bool:
    """Unload a DPD config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
