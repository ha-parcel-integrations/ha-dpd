"""Tests for the DPD integration setup/unload entry points."""

from unittest.mock import AsyncMock, patch

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dpd import DpdData, async_migrate_entry
from custom_components.dpd.api import DpdApiError, DpdAuthError
from custom_components.dpd.const import (
    CONF_BU,
    CONF_COUNTRY,
    CONF_DE_HARDWARE_ID,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_INCLUDE_HISTORY,
    COUNTRY_AT,
    COUNTRY_DE,
    DEFAULT_BU,
    DOMAIN,
    DpdCaptchaError,
)
from custom_components.dpd.countries.de import session as de_session
from custom_components.dpd.countries.de.page import DpdPageError

from .payloads import envelope

_ENTRY_DATA = {
    CONF_EMAIL: "user@example.com",
    CONF_PASSWORD: "secret",
    CONF_BU: DEFAULT_BU,
}


def _add_entry(hass):

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DEFAULT_BU}:{_ENTRY_DATA[CONF_EMAIL]}",
        data=_ENTRY_DATA,
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_setup_entry_succeeds_and_stores_runtime_data(hass):
    entry = _add_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdApiClient.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcels",
            new=AsyncMock(return_value=envelope()),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, DpdData)


@pytest.mark.asyncio
async def test_setup_entry_auth_failure_triggers_reauth(hass):
    entry = _add_entry(hass)
    with patch(
        "custom_components.dpd.DpdApiClient.async_login",
        new=AsyncMock(side_effect=DpdAuthError("nope")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.asyncio
async def test_setup_entry_retries_on_connection_error(hass):
    entry = _add_entry(hass)
    with patch(
        "custom_components.dpd.DpdApiClient.async_login",
        new=AsyncMock(side_effect=aiohttp.ClientError("boom")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_setup_entry_retries_when_first_refresh_fails(hass):
    """Login succeeds but the first data fetch fails.

    The first refresh runs in __init__.py before platforms are forwarded, so a
    failure raises ConfigEntryNotReady from the entry setup (SETUP_RETRY) rather
    than — too late — from a forwarded platform.
    """
    entry = _add_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdApiClient.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcels",
            new=AsyncMock(side_effect=DpdApiError(429)),
        ),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_setup_entry_retries_when_dpd_auth_service_is_down(hass):
    """A 5xx during login (DPD's auth tier outage) must surface as SETUP_RETRY
    so HA backs off and tries again, not as a reauth-prompting auth failure.
    """
    entry = _add_entry(hass)
    with patch(
        "custom_components.dpd.DpdApiClient.async_login",
        new=AsyncMock(side_effect=DpdApiError(503)),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_unload_entry_succeeds(hass):
    entry = _add_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdApiClient.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcels",
            new=AsyncMock(return_value=envelope()),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


# ---------------------------------------------------------------------------
# DPD Germany — separate web-account session, dispatched by CONF_COUNTRY
# ---------------------------------------------------------------------------

_DE_EMAIL = "user@example.de"
_DE_ENTRY_DATA = {
    CONF_EMAIL: _DE_EMAIL,
    CONF_PASSWORD: "secret",
    CONF_COUNTRY: COUNTRY_DE.upper(),
    CONF_DE_HARDWARE_ID: "existing-hw-id",
}


def _add_de_entry(hass):

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"DE:{_DE_EMAIL}",
        data=_DE_ENTRY_DATA,
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_setup_entry_de_succeeds_and_stores_de_session(hass):
    entry = _add_de_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdDeSession.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.coordinator.async_get_all_parcels_de",
            new=AsyncMock(return_value=([], [])),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, DpdData)
    assert entry.runtime_data.client is None
    assert entry.runtime_data.de_session is not None


@pytest.mark.asyncio
async def test_setup_entry_de_uses_a_cookie_jar_of_its_own(hass):
    """DPD's cookies must not land in the jar shared with other integrations."""
    entry = _add_de_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdDeSession.async_login",
            new=AsyncMock(return_value=None),
        ) as login,
        patch(
            "custom_components.dpd.coordinator.async_get_all_parcels_de",
            new=AsyncMock(return_value=([], [])),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    de_http = entry.runtime_data.de_session._session
    assert de_http is not async_get_clientsession(hass)
    assert de_http.cookie_jar is not async_get_clientsession(hass).cookie_jar
    login.assert_awaited_once_with(respect_backoff=True)


@pytest.mark.asyncio
async def test_migration_drops_the_device_id_and_keeps_the_rest(hass):

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"DE:{_DE_EMAIL}",
        data=_DE_ENTRY_DATA,
        options={CONF_DELIVERED_FILTER_TYPE: "days", CONF_DELIVERED_FILTER_AMOUNT: 7},
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "custom_components.dpd.DpdDeSession.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.coordinator.async_get_all_parcels_de",
            new=AsyncMock(return_value=([], [])),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert CONF_DE_HARDWARE_ID not in entry.data
    assert entry.data[CONF_EMAIL] == _DE_EMAIL
    assert entry.data[CONF_PASSWORD] == "secret"
    assert entry.unique_id == f"DE:{_DE_EMAIL}"
    assert entry.options[CONF_DELIVERED_FILTER_AMOUNT] == 7


@pytest.mark.asyncio
async def test_migration_leaves_other_countries_untouched(hass):
    entry = _add_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdApiClient.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcels",
            new=AsyncMock(return_value=envelope()),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.minor_version == 2
    assert dict(entry.data) == _ENTRY_DATA


@pytest.mark.asyncio
async def test_migration_skips_an_entry_already_at_the_current_version(hass):

    entry = MockConfigEntry(domain=DOMAIN, data=_DE_ENTRY_DATA, minor_version=2)
    entry.add_to_hass(hass)
    assert await async_migrate_entry(hass, entry)
    assert entry.data[CONF_DE_HARDWARE_ID] == "existing-hw-id"


@pytest.mark.asyncio
async def test_setup_entry_de_captcha_retries_instead_of_reauth(hass):
    entry = _add_de_entry(hass)
    with patch(
        "custom_components.dpd.DpdDeSession.async_login",
        new=AsyncMock(side_effect=DpdCaptchaError()),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_setup_entry_de_auth_failure_triggers_reauth(hass):
    entry = _add_de_entry(hass)
    with patch(
        "custom_components.dpd.DpdDeSession.async_login",
        new=AsyncMock(side_effect=DpdAuthError("nope")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.asyncio
async def test_setup_entry_de_retries_when_first_refresh_fails(hass):
    entry = _add_de_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdDeSession.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.coordinator.async_get_all_parcels_de",
            new=AsyncMock(side_effect=DpdApiError(500)),
        ),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_unload_entry_de_succeeds(hass):
    entry = _add_de_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdDeSession.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.coordinator.async_get_all_parcels_de",
            new=AsyncMock(return_value=([], [])),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_options_flow_schedules_reload(hass):
    """Submitting the options form schedules a reload of the config entry."""
    entry = _add_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdApiClient.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcels",
            new=AsyncMock(return_value=envelope()),
        ) as mock_get,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        baseline = mock_get.await_count

        result = await hass.config_entries.options.async_init(entry.entry_id)
        await hass.config_entries.options.async_configure(
            result["flow_id"],
            user_input={
                "delivered": {
                    CONF_DELIVERED_FILTER_TYPE: "parcels",
                    CONF_DELIVERED_FILTER_AMOUNT: 14,
                },
                "history": {
                    CONF_INCLUDE_HISTORY: False,
                },
            },
        )
        await hass.async_block_till_done()

    assert mock_get.await_count > baseline


# ---------------------------------------------------------------------------
# DPD Austria — separate mydpd.at session, dispatched by CONF_COUNTRY
# ---------------------------------------------------------------------------

_AT_EMAIL = "user@example.at"
_AT_ENTRY_DATA = {
    CONF_EMAIL: _AT_EMAIL,
    CONF_PASSWORD: "secret",
    CONF_COUNTRY: COUNTRY_AT.upper(),
}


def _add_at_entry(hass):

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"AT:{_AT_EMAIL}",
        data=_AT_ENTRY_DATA,
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.asyncio
async def test_setup_entry_at_succeeds_and_stores_at_session(hass):
    entry = _add_at_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdAtSession.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.countries.at.session.DpdAtSession.async_get_parcels",
            new=AsyncMock(return_value={"inc": [], "send": [], "ret": []}),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, DpdData)
    assert entry.runtime_data.client is None
    assert entry.runtime_data.de_session is None
    assert entry.runtime_data.pl_session is None
    assert entry.runtime_data.at_session is not None


@pytest.mark.asyncio
async def test_setup_entry_at_auth_failure_triggers_reauth(hass):
    entry = _add_at_entry(hass)
    with patch(
        "custom_components.dpd.DpdAtSession.async_login",
        new=AsyncMock(side_effect=DpdAuthError("nope")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR


@pytest.mark.asyncio
async def test_setup_entry_at_retries_when_the_portal_is_rate_limiting(hass):
    """A 429 during setup must retry with backoff, not force reauth."""
    entry = _add_at_entry(hass)
    with patch(
        "custom_components.dpd.DpdAtSession.async_login",
        new=AsyncMock(side_effect=DpdApiError(429)),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_unload_entry_at_succeeds(hass):
    entry = _add_at_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdAtSession.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.countries.at.session.DpdAtSession.async_get_parcels",
            new=AsyncMock(return_value={"inc": [], "send": [], "ret": []}),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.asyncio
async def test_unload_entry_de_closes_its_own_session(hass):
    entry = _add_de_entry(hass)
    with (
        patch(
            "custom_components.dpd.DpdDeSession.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.coordinator.async_get_all_parcels_de",
            new=AsyncMock(return_value=([], [])),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        de_http = entry.runtime_data.de_session._session
        assert not de_http.closed
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()

    assert de_http.closed


@pytest.mark.asyncio
async def test_setup_entry_de_unexpected_page_retries(hass):

    entry = _add_de_entry(hass)
    with patch(
        "custom_components.dpd.DpdDeSession.async_login",
        new=AsyncMock(side_effect=DpdPageError("__VIEWSTATE")),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_setup_entry_de_plain_200_error_retries(hass):
    entry = _add_de_entry(hass)
    with patch(
        "custom_components.dpd.DpdDeSession.async_login",
        new=AsyncMock(side_effect=DpdApiError(200)),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_setup_entry_de_during_the_backoff_retries_without_a_login(hass):

    entry = _add_de_entry(hass)
    de_session._CAPTCHA_UNTIL[_DE_EMAIL.lower()] = float("inf")
    with patch("aiohttp.ClientSession.request") as request:
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    request.assert_not_called()
    assert entry.state is ConfigEntryState.SETUP_RETRY
