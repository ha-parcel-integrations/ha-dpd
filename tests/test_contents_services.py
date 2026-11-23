"""Tests for the contents action, the track_parcel field and the store lifecycle."""

from contextlib import ExitStack
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.dpd.const import (
    CONF_BU,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_REFRESH_TOKEN,
    DEFAULT_BU,
    DOMAIN,
    ParcelStatus,
)
from custom_components.dpd.coordinator import DpdCoordinator
from custom_components.dpd.services import SERVICE_TRACK_PARCEL, SERVICE_UPDATE_CONTENTS

from .payloads import envelope, shipment_sample

LISTED = "01234567890111"
SENT = "01234567890222"
OLD_DELIVERED = "01234567890333"
UNLISTED = "01234567890555"
NEW = "01234567890123"
STORE_KEY = f"{DOMAIN}.contents.%s"
REFRESH = f"{DpdCoordinator.__module__}.DpdCoordinator.async_request_refresh"
LONG_AGO = "2020-01-06T12:00:00"


def _config_entry(email: str = "user@example.test") -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DEFAULT_BU}:{email}",
        data={CONF_EMAIL: email, CONF_PASSWORD: "secret", CONF_BU: DEFAULT_BU},
        options={"delivered_filter_type": "days", "delivered_filter_amount": 7},
    )


@pytest.fixture
def account():
    """The myDPD account, faked: what it lists, and what adding a parcel does."""
    state = {"listed": [LISTED], "sent": [SENT], "old": [OLD_DELIVERED]}

    async def get_parcels():
        return envelope(
            incoming=[
                *[
                    shipment_sample("ORDER_CREATED", parcel_number=n)
                    for n in state["listed"]
                ],
                *[
                    shipment_sample(parcel_number=n, event_dt=LONG_AGO)
                    for n in state["old"]
                ],
            ],
            sending=[
                shipment_sample("ORDER_CREATED", parcel_number=n) for n in state["sent"]
            ],
        )

    async def add_parcel(number):
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
        ),
    ):
        yield state


async def _setup(hass, entry: MockConfigEntry | None = None) -> MockConfigEntry:
    entry = entry or _config_entry()
    entry.add_to_hass(hass)
    if entry.state is not ConfigEntryState.LOADED:
        assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _settle(hass) -> None:
    await hass.async_block_till_done()
    # A refresh requested inside the debouncer's cooldown runs after it.
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=30))
    await hass.async_block_till_done()


async def _update(hass, **data) -> None:
    await hass.services.async_call(DOMAIN, SERVICE_UPDATE_CONTENTS, data, blocking=True)
    await _settle(hass)


def _contents(entry: MockConfigEntry, code: str) -> str | None:
    data = entry.runtime_data.coordinator.data
    parcels = {
        p["barcode"]: p for bucket in data.values() for p in bucket if p["barcode"]
    }
    return parcels[code]["contents"]


def _stored(entry: MockConfigEntry, code: str) -> str | None:
    return entry.runtime_data.coordinator.contents_store.get(code)


async def test_a_parcel_has_no_contents_without_a_stored_value(hass, account):
    entry = await _setup(hass)
    assert _contents(entry, LISTED) is None


async def test_update_contents_sets_replaces_and_clears(hass, account):
    entry = await _setup(hass)

    await _update(hass, tracking_code=LISTED, contents=" Running  shoes ")
    assert _contents(entry, LISTED) == "Running shoes"

    await _update(hass, tracking_code=LISTED, contents="Winter socks")
    assert _contents(entry, LISTED) == "Winter socks"

    await _update(hass, tracking_code=LISTED, contents="  ")
    assert _contents(entry, LISTED) is None


async def test_update_contents_without_text_clears(hass, account):
    entry = await _setup(hass)
    await _update(hass, tracking_code=LISTED, contents="Running shoes")

    await _update(hass, tracking_code=LISTED)

    assert _contents(entry, LISTED) is None


async def test_update_contents_reaches_sent_and_delivered_parcels(hass, account):
    entry = await _setup(hass)

    await _update(hass, tracking_code=SENT, contents="Return label")
    await _update(hass, tracking_code=OLD_DELIVERED, contents="Old order")

    assert _contents(entry, SENT) == "Return label"
    assert _stored(entry, OLD_DELIVERED) == "Old order"


async def test_update_contents_normalizes_the_code(hass, account):
    entry = await _setup(hass)
    await _update(hass, tracking_code=f" {LISTED.lower()} ", contents="Shoes")
    assert _contents(entry, LISTED) == "Shoes"


async def test_update_contents_stores_a_code_not_on_the_account(hass, account):
    entry = await _setup(hass)
    await _update(hass, tracking_code=UNLISTED, contents="Running shoes")
    assert _stored(entry, UNLISTED) == "Running shoes"


async def test_update_contents_refreshes_only_when_something_changed(hass, account):
    await _setup(hass)
    data = {"tracking_code": LISTED, "contents": "Running shoes"}
    with patch(REFRESH, new=AsyncMock()) as refresh:
        await hass.services.async_call(
            DOMAIN, SERVICE_UPDATE_CONTENTS, data, blocking=True
        )
        assert refresh.await_count == 1
        await hass.services.async_call(
            DOMAIN, SERVICE_UPDATE_CONTENTS, data, blocking=True
        )
        assert refresh.await_count == 1
        await hass.services.async_call(
            DOMAIN, SERVICE_UPDATE_CONTENTS, {"tracking_code": UNLISTED}, blocking=True
        )
        assert refresh.await_count == 1


async def test_update_contents_rejects_an_empty_code(hass, account):
    await _setup(hass)
    with pytest.raises(ServiceValidationError, match="tracking code is required"):
        await _update(hass, tracking_code="  ")


async def test_update_contents_needs_a_loaded_account(hass, account):
    entry = await _setup(hass)
    entry.mock_state(hass, ConfigEntryState.SETUP_RETRY)
    try:
        with pytest.raises(ServiceValidationError, match="No DPD account is loaded"):
            await _update(hass, tracking_code=LISTED, contents="Shoes")
    finally:
        entry.mock_state(hass, ConfigEntryState.LOADED)


async def test_a_stored_value_survives_a_restart_of_the_entry(
    hass, hass_storage, account
):
    entry = await _setup(hass)
    await _update(hass, tracking_code=LISTED, contents="Running shoes")
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())

    assert await hass.config_entries.async_unload(entry.entry_id)
    await _setup(hass, entry)

    assert _contents(entry, LISTED) == "Running shoes"


async def test_removing_the_entry_removes_its_store(hass, hass_storage, account):
    entry = await _setup(hass)
    await _update(hass, tracking_code=LISTED, contents="Running shoes")
    store = entry.runtime_data.coordinator.contents_store
    await store._store.async_save(store._data_to_save())
    key = STORE_KEY % entry.entry_id
    assert key in hass_storage

    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


async def test_a_contents_change_alone_fires_no_event(hass, account):
    await _setup(hass)
    events = []
    for name in ("registered", "status_changed", "delivered"):
        hass.bus.async_listen(f"{DOMAIN}_parcel_{name}", events.append)

    await _update(hass, tracking_code=LISTED, contents="Running shoes")

    assert events == []


async def test_known_codes_cover_every_parcel_the_account_lists(hass, account):
    entry = await _setup(hass)
    assert entry.runtime_data.coordinator.known_codes == {
        LISTED,
        SENT,
        OLD_DELIVERED,
    }


async def _two_accounts(hass):
    first = await _setup(hass)
    second = await _setup(hass, _config_entry("other@example.test"))
    return first, second


async def test_several_accounts_store_in_the_one_that_lists_the_code(hass, account):
    first, second = await _two_accounts(hass)
    first.runtime_data.coordinator.known_codes = frozenset()

    with patch(REFRESH, new=AsyncMock()) as refresh:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_CONTENTS,
            {"tracking_code": LISTED, "contents": "Shoes"},
            blocking=True,
        )

    assert _stored(first, LISTED) is None
    assert _stored(second, LISTED) == "Shoes"
    assert refresh.await_count == 1


async def test_a_code_in_two_accounts_is_stored_in_both(hass, account):
    first, second = await _two_accounts(hass)

    with patch(REFRESH, new=AsyncMock()) as refresh:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_CONTENTS,
            {"tracking_code": LISTED, "contents": "Shoes"},
            blocking=True,
        )

    assert _stored(first, LISTED) == "Shoes"
    assert _stored(second, LISTED) == "Shoes"
    assert refresh.await_count == 2


async def test_several_accounts_refresh_only_where_the_value_changed(hass, account):
    first, second = await _two_accounts(hass)
    first.runtime_data.coordinator.contents_store.async_set(LISTED, "Shoes")

    with patch(REFRESH, new=AsyncMock()) as refresh:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_CONTENTS,
            {"tracking_code": LISTED, "contents": "Shoes"},
            blocking=True,
        )

    assert _stored(second, LISTED) == "Shoes"
    assert refresh.await_count == 1


async def test_an_unknown_code_with_several_accounts_needs_an_account(hass, account):
    first, second = await _two_accounts(hass)

    with pytest.raises(ServiceValidationError) as raised:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_UPDATE_CONTENTS,
            {"tracking_code": UNLISTED, "contents": "Shoes"},
            blocking=True,
        )

    assert raised.value.translation_key == "update_contents_entry_required"
    assert _stored(first, UNLISTED) is None
    assert _stored(second, UNLISTED) is None


async def test_a_named_account_stores_even_an_unknown_code(hass, account):
    first, second = await _two_accounts(hass)

    await _update(
        hass,
        tracking_code=UNLISTED,
        contents="Shoes",
        config_entry_id=second.entry_id,
    )

    assert _stored(first, UNLISTED) is None
    assert _stored(second, UNLISTED) == "Shoes"


async def test_a_named_account_limits_a_code_both_accounts_list(hass, account):
    first, second = await _two_accounts(hass)

    await _update(
        hass, tracking_code=LISTED, contents="Shoes", config_entry_id=first.entry_id
    )

    assert _stored(first, LISTED) == "Shoes"
    assert _stored(second, LISTED) is None


async def test_a_named_account_that_is_not_loaded_is_refused(hass, account):
    await _setup(hass)

    with pytest.raises(ServiceValidationError, match="not a loaded DPD account"):
        await _update(
            hass, tracking_code=LISTED, contents="Shoes", config_entry_id="nope"
        )


async def test_track_parcel_stores_the_contents_with_the_new_parcel(hass, account):
    entry = await _setup(hass)

    await hass.services.async_call(
        DOMAIN,
        SERVICE_TRACK_PARCEL,
        {"tracking_code": NEW, "contents": " Garden  hose "},
        blocking=True,
    )

    assert _contents(entry, NEW) == "Garden hose"


async def test_track_parcel_without_contents_leaves_the_stored_value(hass, account):
    entry = await _setup(hass)
    await _update(hass, tracking_code=NEW, contents="Garden hose")

    await hass.services.async_call(
        DOMAIN, SERVICE_TRACK_PARCEL, {"tracking_code": NEW}, blocking=True
    )

    assert _contents(entry, NEW) == "Garden hose"


async def test_track_parcel_replaces_the_contents_of_a_parcel_on_the_account(
    hass, account
):
    entry = await _setup(hass)
    await _update(hass, tracking_code=LISTED, contents="Garden hose")

    with patch(REFRESH, new=AsyncMock()) as refresh:
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TRACK_PARCEL,
            {"tracking_code": LISTED, "contents": "Running shoes"},
            blocking=True,
        )
        assert refresh.await_count == 1
        await hass.services.async_call(
            DOMAIN,
            SERVICE_TRACK_PARCEL,
            {"tracking_code": LISTED, "contents": "Running shoes"},
            blocking=True,
        )
        assert refresh.await_count == 1

    assert _stored(entry, LISTED) == "Running shoes"


COUNTRY_CODE = "01234567890999"


def _country_entry(country: str) -> MockConfigEntry:
    data = {CONF_EMAIL: "user@example.test", CONF_PASSWORD: "secret"}
    if country == "UK":
        data = {CONF_REFRESH_TOKEN: "token", CONF_DEVICE_ID: "device"}
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{country}:user@example.test",
        data={**data, CONF_COUNTRY: country},
        options={"delivered_filter_type": "days", "delivered_filter_amount": 7},
    )


def _patch_country(country: str):
    """Patch the one country's session so its entry lists COUNTRY_CODE."""
    session = MagicMock()
    session.async_login = AsyncMock(return_value=None)
    session.refresh_token = "token"
    if country == "DE":
        parcel = {
            "carrier": "DPD",
            "barcode": COUNTRY_CODE,
            "status": ParcelStatus.OUT_FOR_DELIVERY,
            "delivered": False,
            "delivered_at": None,
            "planned_from": None,
            "planned_to": None,
            "contents": None,
            "raw": {},
        }
        return (
            patch("custom_components.dpd.DpdDeSession", return_value=session),
            patch(
                f"{DpdCoordinator.__module__}.async_get_all_parcels_de",
                # A fresh dict per poll, as the real normaliser returns.
                new=AsyncMock(side_effect=lambda *a, **k: ([dict(parcel)], [])),
            ),
        )
    if country == "AT":
        session.async_get_parcels = AsyncMock(
            return_value={
                "inc": [
                    {
                        "parcelno": COUNTRY_CODE,
                        "type": "inc",
                        "verified": True,
                        "lifecycle": {"state": 2, "state_info": "", "entries": []},
                    }
                ],
                "send": [],
                "ret": [],
            }
        )
        return (patch("custom_components.dpd.DpdAtSession", return_value=session),)
    if country == "PL":
        session.async_get_parcels = AsyncMock(
            return_value=[
                {
                    "waybill": COUNTRY_CODE,
                    "sender": {"name": "Example Shop"},
                    "main_status": {
                        "status": "HANDED_OVER_FOR_DELIVERY",
                        "date": "2026-09-09T08:00:00Z",
                    },
                    "statuses": [],
                    "delivery": {"planned_delivery_date": None},
                }
            ]
        )
        session.async_get_parcel_detail = AsyncMock(return_value={})
        return (patch("custom_components.dpd.DpdPlSession", return_value=session),)
    session.async_get_parcels = AsyncMock(
        return_value=(
            [
                {
                    "parcelNumber": COUNTRY_CODE,
                    "parcelCode": f"{COUNTRY_CODE}*001",
                    "deliveredToConsumer": False,
                    "undelivered": False,
                    "collectedFromShop": False,
                }
            ],
            [],
        )
    )
    return (patch("custom_components.dpd.DpdUkSession", return_value=session),)


@pytest.mark.parametrize("country", ["DE", "AT", "PL", "UK"])
async def test_update_contents_works_on_every_country_source(hass, country):
    entry = _country_entry(country)
    with ExitStack() as stack:
        for patcher in _patch_country(country):
            stack.enter_context(patcher)
        await _setup(hass, entry)

        assert _contents(entry, COUNTRY_CODE) is None

        await _update(hass, tracking_code=COUNTRY_CODE, contents=" Running  shoes ")
        assert _contents(entry, COUNTRY_CODE) == "Running shoes"

        await _update(hass, tracking_code=COUNTRY_CODE, contents="Winter socks")
        assert _contents(entry, COUNTRY_CODE) == "Winter socks"

        await _update(hass, tracking_code=COUNTRY_CODE, contents="")
        assert _contents(entry, COUNTRY_CODE) is None
