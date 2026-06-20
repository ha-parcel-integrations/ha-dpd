"""Tests for the Follow My Parcel methods on DpdApiClient.

The main parcels / auth API has broader test coverage planned in
DPD 2.0.0 Phase 6d (mirroring ha-dhl-nl's test_api.py). This file
focuses on the FMP sub-API shipped in 1.3.0.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dpd.api import DpdApiClient


def _mock_response(status: int, payload: dict | None) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=payload)
    return response


def _mock_session(*responses: MagicMock) -> MagicMock:
    """Return a session whose `.post`/`.get` yield ``responses`` in order."""
    queue = list(responses)

    @asynccontextmanager
    async def _ctx(*_args, **_kwargs):
        yield queue.pop(0)

    session = MagicMock()
    session.post = MagicMock(side_effect=_ctx)
    session.get = MagicMock(side_effect=_ctx)
    return session


def _client(session: MagicMock, token: str | None = "main-token") -> DpdApiClient:
    client = DpdApiClient(
        email="user@example.com",
        password="secret",
        session=session,
    )
    client._token = token  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_fmp_delivery_window_returns_window_on_happy_path():
    window = {
        "deliveryDate": "2026-06-17",
        "timeRange": {"from": "10:34:00", "to": "11:34:00"},
    }
    session = _mock_session(
        _mock_response(200, {"access_token": "fmp-token"}),
        _mock_response(200, {"deliveryDateAndTime": window}),
    )
    client = _client(session)

    assert await client.async_fmp_delivery_window("abc") == window


@pytest.mark.asyncio
async def test_fmp_delivery_window_returns_none_when_authenticate_fails():
    session = _mock_session(_mock_response(500, None))
    client = _client(session)
    assert await client.async_fmp_delivery_window("abc") is None


@pytest.mark.asyncio
async def test_fmp_delivery_window_returns_none_when_authenticate_lacks_token():
    session = _mock_session(_mock_response(200, {}))
    client = _client(session)
    assert await client.async_fmp_delivery_window("abc") is None


@pytest.mark.asyncio
async def test_fmp_delivery_window_returns_none_when_shipment_fetch_fails():
    session = _mock_session(
        _mock_response(200, {"access_token": "fmp-token"}),
        _mock_response(503, None),
    )
    client = _client(session)
    assert await client.async_fmp_delivery_window("abc") is None


@pytest.mark.asyncio
async def test_fmp_delivery_window_returns_none_when_payload_has_no_delivery_date():
    session = _mock_session(
        _mock_response(200, {"access_token": "fmp-token"}),
        _mock_response(200, {"someOtherField": True}),
    )
    client = _client(session)
    assert await client.async_fmp_delivery_window("abc") is None


@pytest.mark.asyncio
async def test_fmp_authenticate_uses_hashcode_credentials():
    session = _mock_session(
        _mock_response(200, {"access_token": "fmp-token"}),
        _mock_response(200, {"deliveryDateAndTime": {"deliveryDate": "x"}}),
    )
    client = _client(session)

    await client.async_fmp_delivery_window("the-hashcode")

    # First call was the authenticate POST; check body shape
    post_call = session.post.call_args_list[0]
    assert post_call.kwargs["json"] == {
        "authMethod": "HASHCODE",
        "credentials": "the-hashcode",
    }
