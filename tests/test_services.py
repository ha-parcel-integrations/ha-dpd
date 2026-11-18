"""Tests for the dpd.track_parcel action."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dpd.const import (
    CONF_BU,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    DEFAULT_BU,
    DOMAIN,
    DpdApiError,
    DpdParcelNotFoundError,
)
from custom_components.dpd.services import SERVICE_TRACK_PARCEL

from .payloads import envelope, shipment_sample

NEW = "01234567890123"
OLD = "01234567890999"
# Delivered well before the 7-day delivered-parcels window of _add_entry.
LONG_AGO = "2020-01-06T12:00:00"


def _add_entry(hass, email="user@example.com"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DEFAULT_BU}:{email}",
        data={CONF_EMAIL: email, CONF_PASSWORD: "secret", CONF_BU: DEFAULT_BU},
        options={
            CONF_DELIVERED_FILTER_TYPE: "days",
            CONF_DELIVERED_FILTER_AMOUNT: 7,
        },
    )
    entry.add_to_hass(hass)
    return entry


def _on_account(state):
    def parcel(number):
        if number in state["delivered"]:
            return shipment_sample(parcel_number=number, event_dt=LONG_AGO)
        return shipment_sample(description="ORDER_CREATED", parcel_number=number)

    return envelope(
        incoming=[parcel(n) for n in state["listed"]],
        sending=[parcel(n) for n in state["sent"]],
    )


async def _call(hass, **data):
    await hass.services.async_call(DOMAIN, SERVICE_TRACK_PARCEL, data, blocking=True)


@pytest.fixture
def dpd():
    """The account, faked: parcels on it, and what adding does."""
    state = {
        "listed": [OLD],
        "sent": [],
        "delivered": set(),
        "add_error": None,
        "adds_it": True,
    }

    async def get_parcels():
        return _on_account(state)

    async def add_parcel(number):
        if state["add_error"]:
            raise state["add_error"]
        if state["adds_it"]:
            state["listed"].append(number)

    with (
        patch(
            "custom_components.dpd.DpdApiClient.async_login",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcels",
            new=AsyncMock(side_effect=get_parcels),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcel_detail",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.dpd.DpdApiClient.async_add_parcel",
            new=AsyncMock(side_effect=add_parcel),
        ) as add,
    ):
        state["add"] = add
        yield state


async def _setup(hass, *entries):
    # Setting up one entry loads the integration, which sets up every entry
    # of the domain; only the ones still waiting are set up here.
    for entry in entries:
        if entry.state is not ConfigEntryState.LOADED:
            assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert all(entry.state is ConfigEntryState.LOADED for entry in entries)


@pytest.mark.asyncio
async def test_the_action_exists_without_any_entry(hass):
    assert await async_setup_component(hass, DOMAIN, {})
    assert hass.services.has_service(DOMAIN, SERVICE_TRACK_PARCEL)
    with pytest.raises(ServiceValidationError, match="No DPD account is loaded"):
        await _call(hass, tracking_code=NEW)


@pytest.mark.asyncio
async def test_adds_the_parcel_and_refreshes(hass, dpd):
    entry = _add_entry(hass)
    await _setup(hass, entry)
    await _call(hass, tracking_code="0123 4567 8901 23")
    dpd["add"].assert_awaited_once_with(NEW)
    barcodes = {
        p["barcode"] for p in entry.runtime_data.coordinator.data["incoming_active"]
    }
    assert barcodes == {OLD, NEW}


@pytest.mark.asyncio
async def test_a_parcel_already_on_the_account_is_left_alone(hass, dpd):
    await _setup(hass, _add_entry(hass))
    await _call(hass, tracking_code=OLD)
    dpd["add"].assert_not_awaited()


@pytest.mark.asyncio
async def test_parcels_the_sensors_leave_out_are_still_on_the_account(hass, dpd):
    # Delivered before the delivered-parcels window, or sent rather than
    # received: on the account all the same, so nothing to add.
    dpd["delivered"].add(OLD)
    dpd["sent"].append(NEW)
    entry = _add_entry(hass)
    await _setup(hass, entry)
    assert not entry.runtime_data.coordinator.data["incoming_delivered"]
    await _call(hass, tracking_code=OLD)
    await _call(hass, tracking_code=NEW)
    dpd["add"].assert_not_awaited()


@pytest.mark.asyncio
async def test_adds_a_parcel_delivered_before_the_window(hass, dpd):
    entry = _add_entry(hass)
    await _setup(hass, entry)
    dpd["delivered"].add(NEW)
    await _call(hass, tracking_code=NEW)
    dpd["add"].assert_awaited_once_with(NEW)
    assert not entry.runtime_data.coordinator.data["incoming_delivered"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["123", "0123456789012A", "012345678901234"])
async def test_not_a_parcel_number(hass, dpd, code):
    await _setup(hass, _add_entry(hass))
    with pytest.raises(ServiceValidationError, match="not a DPD parcel number"):
        await _call(hass, tracking_code=code)
    dpd["add"].assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_to_dpd(hass, dpd):
    await _setup(hass, _add_entry(hass))
    dpd["add_error"] = DpdParcelNotFoundError()
    with pytest.raises(ServiceValidationError, match="does not know parcel"):
        await _call(hass, tracking_code=NEW)


@pytest.mark.asyncio
async def test_dpd_answers_but_the_parcel_does_not_appear(hass, dpd):
    await _setup(hass, _add_entry(hass))
    dpd["adds_it"] = False
    with pytest.raises(HomeAssistantError, match="did not add it"):
        await _call(hass, tracking_code=NEW)


@pytest.mark.asyncio
async def test_dpd_unreachable(hass, dpd):
    await _setup(hass, _add_entry(hass))
    dpd["add_error"] = DpdApiError(502)
    with pytest.raises(HomeAssistantError, match="Could not reach DPD"):
        await _call(hass, tracking_code=NEW)


@pytest.mark.asyncio
async def test_only_the_mydpd_app_account_can_add(hass, dpd):
    entry = _add_entry(hass)
    await _setup(hass, entry)
    entry.runtime_data.client = None  # what every other transport has
    with pytest.raises(ServiceValidationError, match="myDPD app accounts"):
        await _call(hass, tracking_code=NEW)
    dpd["add"].assert_not_awaited()


@pytest.mark.asyncio
async def test_two_accounts_need_a_choice(hass, dpd):
    first = _add_entry(hass, "one@example.com")
    second = _add_entry(hass, "two@example.com")
    await _setup(hass, first, second)
    with pytest.raises(ServiceValidationError, match="loaded DPD account"):
        await _call(hass, tracking_code=NEW)

    await _call(hass, tracking_code=NEW, config_entry_id=second.entry_id)
    dpd["add"].assert_awaited_once_with(NEW)

    with pytest.raises(ServiceValidationError, match="loaded DPD account"):
        await _call(hass, tracking_code=NEW, config_entry_id="nope")


@pytest.mark.asyncio
async def test_unloading_one_account_leaves_the_action(hass, dpd):
    first = _add_entry(hass, "one@example.com")
    second = _add_entry(hass, "two@example.com")
    await _setup(hass, first, second)
    assert await hass.config_entries.async_unload(first.entry_id)
    await hass.async_block_till_done()
    assert hass.services.has_service(DOMAIN, SERVICE_TRACK_PARCEL)
    await _call(hass, tracking_code=NEW)
    dpd["add"].assert_awaited_once_with(NEW)


async def test_the_refresh_after_adding_fails(hass, dpd):
    entry = _add_entry(hass)
    await _setup(hass, entry)
    with (
        patch(
            "custom_components.dpd.DpdApiClient.async_get_parcels",
            new=AsyncMock(side_effect=DpdApiError(503)),
        ),
        pytest.raises(HomeAssistantError, match="confirm the parcel"),
    ):
        await _call(hass, tracking_code=NEW)
