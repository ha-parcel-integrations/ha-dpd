"""DPD UK consumer-app stack: config flow, setup, coordinator, diagnostics."""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_EMAIL
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.dpd import DpdData
from custom_components.dpd.const import (
    CONF_BU,
    CONF_COUNTRY,
    CONF_DELIVERED_FILTER_AMOUNT,
    CONF_DELIVERED_FILTER_TYPE,
    CONF_DEVICE_ID,
    CONF_INCLUDE_HISTORY,
    CONF_MAGIC_LINK,
    CONF_REFRESH_TOKEN,
    CONF_UK_LINK_SOURCE,
    COUNTRY_UK,
    DOMAIN,
    DpdApiError,
    DpdAuthError,
)
from custom_components.dpd.coordinator import DpdCoordinator
from custom_components.dpd.countries.uk.session import (
    DpdUkEmailRequiredError,
    DpdUkLinkError,
    DpdUkNotConsumerError,
    DpdUkUnrecognisedLinkError,
)
from custom_components.dpd.diagnostics import async_get_config_entry_diagnostics

EMAIL = "me@example.invalid"
LINK = "https://example.invalid/action?oobCode=abc"
DELIVERED = {CONF_DELIVERED_FILTER_TYPE: "days", CONF_DELIVERED_FILTER_AMOUNT: 7}
ENTRY_DATA = {
    CONF_EMAIL: EMAIL,
    CONF_COUNTRY: COUNTRY_UK.upper(),
    CONF_REFRESH_TOKEN: "stored-refresh",
    CONF_DEVICE_ID: "dev-1",
}
SESSION = "custom_components.dpd.config_flow.DpdUkSession"


def _signed_in(consumer="c-1", refresh="fresh-refresh"):
    async def _sign_in(self, email, link):
        self.refresh_token = refresh
        return consumer

    return patch(f"{SESSION}.async_sign_in_with_link", new=_sign_in)


async def _to_request(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_BU: "dpd-uk-app"}
    )
    assert result["step_id"] == "request"
    return result


async def test_flow_pastes_the_link_and_creates_a_passwordless_entry(hass):
    with _signed_in():
        result = await _to_request(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            user_input={CONF_EMAIL: f" {EMAIL} ", CONF_MAGIC_LINK: LINK},
        )
        assert result["step_id"] == "delivered"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=DELIVERED
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == EMAIL
    data = result["data"]
    assert data[CONF_COUNTRY] == "UK"
    assert data[CONF_REFRESH_TOKEN] == "fresh-refresh"
    assert len(data[CONF_DEVICE_ID]) == 32
    assert "password" not in data
    assert CONF_MAGIC_LINK not in data
    assert result["result"].unique_id == "UK:c-1"


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (DpdUkLinkError("x"), "link_expired"),
        (DpdUkNotConsumerError("x"), "not_a_consumer"),
        (DpdAuthError("x"), "invalid_auth"),
        (DpdApiError(500), "cannot_connect"),
        (aiohttp.ClientError(), "cannot_connect"),
    ],
)
async def test_flow_maps_each_error(hass, error, key):
    with patch(f"{SESSION}.async_sign_in_with_link", new=AsyncMock(side_effect=error)):
        result = await _to_request(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: EMAIL, CONF_MAGIC_LINK: LINK}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}


async def test_flow_aborts_when_the_account_is_already_configured(hass):
    MockConfigEntry(domain=DOMAIN, unique_id="UK:c-1", data=ENTRY_DATA).add_to_hass(
        hass
    )
    with _signed_in():
        result = await _to_request(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: EMAIL, CONF_MAGIC_LINK: LINK}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_takes_a_new_link_and_keeps_the_device_id(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="UK:c-1", data=ENTRY_DATA)
    entry.add_to_hass(hass)
    with (
        _signed_in(),
        patch(
            "homeassistant.config_entries.ConfigEntries.async_reload",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await entry.start_reauth_flow(hass)
        assert result["step_id"] == "request"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: EMAIL, CONF_MAGIC_LINK: LINK}
        )
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_REFRESH_TOKEN] == "fresh-refresh"
    assert entry.data[CONF_DEVICE_ID] == "dev-1"
    assert entry.data[CONF_UK_LINK_SOURCE] == "app"


async def test_reauth_with_a_different_account_aborts(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id="UK:c-1", data=ENTRY_DATA)
    entry.add_to_hass(hass)
    with _signed_in(consumer="c-other"):
        result = await entry.start_reauth_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: EMAIL, CONF_MAGIC_LINK: LINK}
        )
    assert result["reason"] == "unique_id_mismatch"
    assert entry.data[CONF_REFRESH_TOKEN] == "stored-refresh"


def _add_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="UK:c-1", data=ENTRY_DATA, options=DELIVERED
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_stores_the_uk_session_and_persists_a_rotated_token(hass):
    entry = _add_entry(hass)

    async def _login(self):
        self._token_updater("rotated")

    with (
        patch("custom_components.dpd.DpdUkSession.async_login", new=_login),
        patch(
            "custom_components.dpd.countries.uk.session.DpdUkSession.async_get_parcels",
            new=AsyncMock(return_value=([{"parcelNumber": "P1"}], [])),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, DpdData)
    assert entry.runtime_data.uk_session is not None
    assert entry.runtime_data.client is None
    assert entry.data[CONF_REFRESH_TOKEN] == "rotated"
    assert entry.runtime_data.coordinator.data["incoming_active"][0]["barcode"] == "P1"


@pytest.mark.parametrize(
    ("error", "state"),
    [
        (DpdAuthError("x"), ConfigEntryState.SETUP_ERROR),
        (DpdUkNotConsumerError("x"), ConfigEntryState.SETUP_ERROR),
        (DpdApiError(503), ConfigEntryState.SETUP_RETRY),
        (aiohttp.ClientError(), ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_failures(hass, error, state):
    entry = _add_entry(hass)
    with patch(
        "custom_components.dpd.DpdUkSession.async_login",
        new=AsyncMock(side_effect=error),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is state


def _coordinator(hass, uk):
    entry = MockConfigEntry(domain=DOMAIN, data=ENTRY_DATA, options=DELIVERED)
    entry.add_to_hass(hass)
    return DpdCoordinator(hass, None, entry, uk_session=uk)


async def test_coordinator_empty_lists_are_a_valid_inbox(hass):
    uk = MagicMock()
    uk.async_get_parcels = AsyncMock(return_value=([], []))
    result = await _coordinator(hass, uk)._async_update_data()
    assert result == {
        "incoming_active": [],
        "incoming_delivered": [],
        "outgoing_active": [],
        "outgoing_delivered": [],
    }


async def test_coordinator_splits_by_bucket_and_never_has_outgoing(hass):
    uk = MagicMock()
    uk.async_get_parcels = AsyncMock(
        return_value=([{"parcelNumber": "A"}], [{"parcelNumber": "D"}])
    )
    result = await _coordinator(hass, uk)._async_update_data()
    assert [p["barcode"] for p in result["incoming_active"]] == ["A"]
    assert [p["barcode"] for p in result["incoming_delivered"]] == ["D"]
    assert result["outgoing_active"] == result["outgoing_delivered"] == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (DpdAuthError("x"), ConfigEntryAuthFailed),
        (DpdUkNotConsumerError("x"), ConfigEntryAuthFailed),
        (DpdApiError(500), UpdateFailed),
    ],
)
async def test_coordinator_failures(hass, error, expected):
    uk = MagicMock()
    uk.async_get_parcels = AsyncMock(side_effect=error)
    with pytest.raises(expected):
        await _coordinator(hass, uk)._async_update_data()


async def test_coordinator_files_a_parcel_by_its_status_not_its_bucket(hass):
    uk = MagicMock()
    uk.async_get_parcels = AsyncMock(
        return_value=(
            [{"parcelNumber": "A", "deliveredToConsumer": True}],
            [{"parcelNumber": "P", "parcelStatusType": 5}],
        )
    )
    result = await _coordinator(hass, uk)._async_update_data()
    assert [p["barcode"] for p in result["incoming_active"]] == ["P"]
    assert [p["barcode"] for p in result["incoming_delivered"]] == ["A"]


def _history_coordinator(hass, uk):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=ENTRY_DATA,
        options={**DELIVERED, CONF_INCLUDE_HISTORY: True},
    )
    entry.add_to_hass(hass)
    return DpdCoordinator(hass, None, entry, uk_session=uk)


async def test_history_is_read_once_until_the_status_moves(hass):
    parcel = {
        "parcelNumber": "A",
        "parcelCode": "A*1",
        "parcelStatusType": 8,
        "trackingStatusCurrent": "Your parcel is at our depot",
    }
    uk = MagicMock()
    uk.async_get_parcels = AsyncMock(return_value=([parcel, {"parcelNumber": "B"}], []))
    uk.async_get_parcel_events = AsyncMock(
        return_value=[
            {
                "eventCode": "DPT",
                "eventDate": "2026-07-01 08:00:00",
                "eventText": "Your parcel is at our depot",
            }
        ]
    )
    coordinator = _history_coordinator(hass, uk)

    result = await coordinator._async_update_data()
    await coordinator._async_update_data()

    uk.async_get_parcel_events.assert_awaited_once_with("A*1")
    by_barcode = {p["barcode"]: p for p in result["incoming_active"]}
    assert by_barcode["A"]["history"][0]["raw_status"] == "DPT"
    assert by_barcode["B"]["history"] == []

    uk.async_get_parcels.return_value = (
        [{**parcel, "parcelStatusType": 6}, {"parcelNumber": "B"}],
        [],
    )
    await coordinator._async_update_data()
    assert uk.async_get_parcel_events.await_count == 2


async def test_a_failed_history_read_leaves_an_empty_timeline(hass):
    uk = MagicMock()
    uk.async_get_parcels = AsyncMock(
        return_value=([{"parcelCode": "A*1", "parcelNumber": "A"}], [])
    )
    uk.async_get_parcel_events = AsyncMock(return_value=None)
    result = await _history_coordinator(hass, uk)._async_update_data()
    assert result["incoming_active"][0]["history"] == []


async def test_history_off_never_asks_for_events(hass):
    uk = MagicMock()
    uk.async_get_parcels = AsyncMock(
        return_value=([{"parcelCode": "A*1", "parcelNumber": "A"}], [])
    )
    uk.async_get_parcel_events = AsyncMock()
    result = await _coordinator(hass, uk)._async_update_data()
    uk.async_get_parcel_events.assert_not_awaited()
    assert result["incoming_active"][0]["history"] is None


async def test_a_rejected_history_read_needs_reauth(hass):
    uk = MagicMock()
    uk.async_get_parcels = AsyncMock(
        return_value=([{"parcelCode": "A*1", "parcelNumber": "A"}], [])
    )
    uk.async_get_parcel_events = AsyncMock(side_effect=DpdAuthError("x"))
    with pytest.raises(ConfigEntryAuthFailed):
        await _history_coordinator(hass, uk)._async_update_data()


async def test_diagnostics_keeps_only_the_allowlisted_fields_of_the_record():
    coordinator = MagicMock()
    parcel = {
        "barcode": "P1",
        "sender": "Sender Shop",
        "url": "https://track.dpd.co.uk/parcels/P1*001",
        "status": "in_transit",
        "raw_status": "Left with your neighbour at 3 Example Road",
        "planned_from": "2026-07-01T09:30:00+01:00",
        "history": [{"timestamp": "2026-07-01T08:00:00+01:00", "raw_status": "OFD"}],
        "raw": {
            "parcelStatusType": 6,
            "undelivered": False,
            "estimatedDeliveryStartTime": "09:30",
            "outForDeliveryDetails": {"outForDeliveryDate": ["2026-07-01"]},
            "deliveryDetails": {
                "totalWeight": 2,
                "podDetails": {"podDate": "2026-07-01 08:15:00", "podName": "J DOE"},
                "address": {"street": "1 Example Road", "postcode": "AB1 2CD"},
                "notificationDetails": {"email": "jane@example.invalid"},
            },
            "issueDetails": {"issueCode": "I1", "note": "Call Jane"},
            "pickupType": {"unexpected": "object"},
            "shipperDetails": {"accountNumber": "ACC1", "customerDisplayName": "S"},
            "recipientName": "Jane Doe",
            "somethingNew": "private",
        },
    }
    coordinator.data = {"incoming_active": [parcel]}
    coordinator.update_interval = None
    entry = MagicMock()
    entry.data = {
        **ENTRY_DATA,
        "consumerId": "c-1",
        "dpdSession": "s",
        "idToken": "t",
    }
    entry.options = {}
    entry.runtime_data = DpdData(client=None, coordinator=coordinator)

    result = await async_get_config_entry_diagnostics(MagicMock(), entry)

    text = str(result)
    for secret in (
        EMAIL,
        "stored-refresh",
        "dev-1",
        "c-1",
        "Jane Doe",
        "J DOE",
        "Call Jane",
        "jane@example.invalid",
        "Example Road",
        "AB1 2CD",
        "ACC1",
        "private",
        "unexpected",
        "Sender Shop",
        "P1",
    ):
        assert secret not in text
    shown = result["incoming_active"][0]
    assert shown["status"] == "in_transit"
    assert shown["planned_from"] == "2026-07-01T09:30:00+01:00"
    assert shown["history"][0]["raw_status"] == "OFD"
    assert shown["raw"] == {
        "parcelStatusType": 6,
        "undelivered": False,
        "estimatedDeliveryStartTime": "09:30",
        "outForDeliveryDetails": {"outForDeliveryDate": ["2026-07-01"]},
        "deliveryDetails": {
            "totalWeight": 2,
            "podDetails": {"podDate": "2026-07-01 08:15:00"},
        },
        "issueDetails": {"issueCode": "I1"},
    }


async def test_flow_website_link_needs_no_email_and_stores_its_source(hass):
    async def _sign_in(self, email, link):
        assert email == ""
        self.source = "web"
        self.refresh_token = "web-refresh"
        return "c-1"

    with patch(f"{SESSION}.async_sign_in_with_link", new=_sign_in):
        result = await _to_request(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_MAGIC_LINK: LINK}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=DELIVERED
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_UK_LINK_SOURCE] == "web"
    assert result["data"][CONF_REFRESH_TOKEN] == "web-refresh"


async def test_flow_app_link_stores_the_app_source(hass):
    with _signed_in():
        result = await _to_request(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: EMAIL, CONF_MAGIC_LINK: LINK}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input=DELIVERED
        )
    assert result["data"][CONF_UK_LINK_SOURCE] == "app"


@pytest.mark.parametrize(
    ("error", "field", "key"),
    [
        (DpdUkEmailRequiredError("x"), CONF_EMAIL, "email_required"),
        (DpdUkUnrecognisedLinkError("x"), CONF_MAGIC_LINK, "unrecognised_link"),
    ],
)
async def test_flow_field_errors(hass, error, field, key):
    with patch(f"{SESSION}.async_sign_in_with_link", new=AsyncMock(side_effect=error)):
        result = await _to_request(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_MAGIC_LINK: LINK}
        )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {field: key}


async def test_setup_passes_the_stored_source_to_the_session(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="UK:c-1",
        data={**ENTRY_DATA, CONF_UK_LINK_SOURCE: "web"},
        options=DELIVERED,
    )
    entry.add_to_hass(hass)
    seen = []

    async def _login(self):
        seen.append(self.source)

    with (
        patch("custom_components.dpd.DpdUkSession.async_login", new=_login),
        patch(
            "custom_components.dpd.countries.uk.session.DpdUkSession.async_get_parcels",
            new=AsyncMock(return_value=([], [])),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert seen == ["web"]
