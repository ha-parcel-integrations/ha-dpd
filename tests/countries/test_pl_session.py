"""Tests for DPD Polska's public OAuth session: SMS registration, token
refresh/rotation, and the 401-retry-once authorized-request path.
"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dpd.const import DpdApiError, DpdAuthError
from custom_components.dpd.countries.pl.session import DpdPlSession

pytestmark = pytest.mark.asyncio


def _mock_response(status: int, body: object) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=body)
    return response


def _queue_ctx(*responses: MagicMock):
    queue = list(responses)

    @asynccontextmanager
    async def _ctx(*_args, **_kwargs):
        yield queue.pop(0)

    return MagicMock(side_effect=_ctx)


def _mock_session(*, put=None, post=None, request=None) -> MagicMock:
    session = MagicMock()
    if put is not None:
        session.put = _queue_ctx(*put)
    if post is not None:
        session.post = _queue_ctx(*post)
    if request is not None:
        session.request = _queue_ctx(*request)
    return session


async def test_send_sms_raises_auth_error_on_rejected_phone_number():
    session = _mock_session(put=[_mock_response(400, {})])
    pl = DpdPlSession(session)

    with pytest.raises(DpdAuthError):
        await pl.async_send_sms("600000000")


async def test_send_sms_raises_api_error_on_server_failure():
    session = _mock_session(put=[_mock_response(500, {})])
    pl = DpdPlSession(session)

    with pytest.raises(DpdApiError):
        await pl.async_send_sms("600000000")


async def test_register_exchanges_sms_code_for_tokens_and_stores_refresh_token():
    session = _mock_session(
        post=[
            _mock_response(200, {"code": "auth-code"}),
            _mock_response(
                200,
                {"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 300},
            ),
        ]
    )
    updater = MagicMock()
    pl = DpdPlSession(session, token_updater=updater)

    await pl.async_register("600000000", "123456")

    assert pl.refresh_token == "rt-1"
    assert pl._access_token == "at-1"
    updater.assert_called_once_with("rt-1")


async def test_register_raises_auth_error_when_sms_code_rejected():
    session = _mock_session(post=[_mock_response(401, {})])
    pl = DpdPlSession(session)

    with pytest.raises(DpdAuthError):
        await pl.async_register("600000000", "000000")


async def test_register_raises_auth_error_when_no_authorization_code_returned():
    session = _mock_session(post=[_mock_response(200, {})])
    pl = DpdPlSession(session)

    with pytest.raises(DpdAuthError):
        await pl.async_register("600000000", "123456")


async def test_login_refreshes_using_stored_refresh_token():
    session = _mock_session(
        post=[_mock_response(200, {"access_token": "at-2", "expires_in": 300})]
    )
    pl = DpdPlSession(session, refresh_token="stored-rt")

    await pl.async_login()

    assert pl._access_token == "at-2"
    # A refresh response with no refresh_token in the body keeps the one
    # already on hand instead of clearing it.
    assert pl.refresh_token == "stored-rt"


async def test_login_without_a_refresh_token_raises_auth_error():
    pl = DpdPlSession(MagicMock())

    with pytest.raises(DpdAuthError):
        await pl.async_login()


async def test_refresh_raises_auth_error_on_rejected_grant():
    session = _mock_session(post=[_mock_response(401, {})])
    pl = DpdPlSession(session, refresh_token="stale-rt")

    with pytest.raises(DpdAuthError):
        await pl.async_login()


async def test_authorized_request_refreshes_when_no_token_cached():
    session = _mock_session(
        post=[_mock_response(200, {"access_token": "at-1", "expires_in": 300})],
        request=[_mock_response(200, {"packages": []})],
    )
    pl = DpdPlSession(session, refresh_token="rt-1")

    result = await pl.async_get_parcels()

    assert result == []


async def test_authorized_request_reuses_unexpired_token_without_refreshing():
    session = _mock_session(request=[_mock_response(200, {"packages": []})])
    pl = DpdPlSession(session, refresh_token="rt-1")
    pl._access_token = "still-valid"
    pl._expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    result = await pl.async_get_parcels()

    assert result == []


async def test_authorized_request_retries_once_on_401_then_succeeds():
    session = _mock_session(
        post=[
            _mock_response(200, {"access_token": "at-1", "expires_in": 300}),
            _mock_response(200, {"access_token": "at-2", "expires_in": 300}),
        ],
        request=[
            _mock_response(401, {}),
            _mock_response(200, {"packages": []}),
        ],
    )
    pl = DpdPlSession(session, refresh_token="rt-1")

    result = await pl.async_get_parcels()

    assert result == []
    assert pl._access_token == "at-2"


async def test_authorized_request_raises_auth_error_when_second_attempt_also_401():
    session = _mock_session(
        post=[
            _mock_response(200, {"access_token": "at-1", "expires_in": 300}),
            _mock_response(200, {"access_token": "at-2", "expires_in": 300}),
        ],
        request=[
            _mock_response(401, {}),
            _mock_response(401, {}),
        ],
    )
    pl = DpdPlSession(session, refresh_token="rt-1")

    with pytest.raises(DpdAuthError):
        await pl.async_get_parcels()


async def test_get_parcels_filters_out_non_dict_entries_and_ignores_missing_key():
    session = _mock_session(request=[_mock_response(200, {"packages": [{"waybill": "A"}, "garbage"]})])
    pl = DpdPlSession(session, refresh_token="rt-1")
    pl._access_token = "valid"
    pl._expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    result = await pl.async_get_parcels()

    assert result == [{"waybill": "A"}]


async def test_get_parcels_returns_empty_list_when_packages_key_missing():
    session = _mock_session(request=[_mock_response(200, {})])
    pl = DpdPlSession(session, refresh_token="rt-1")
    pl._access_token = "valid"
    pl._expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    result = await pl.async_get_parcels()

    assert result == []


async def test_get_parcel_detail_returns_body_for_authenticated_request():
    session = _mock_session(request=[_mock_response(200, {"waybill": "A", "extra": True})])
    pl = DpdPlSession(session, refresh_token="rt-1")
    pl._access_token = "valid"
    pl._expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)

    result = await pl.async_get_parcel_detail("A")

    assert result == {"waybill": "A", "extra": True}
