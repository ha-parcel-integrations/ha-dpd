"""Tests for DPD Austria's mydpd.at portal session.

Covers the login/JWT exchange, the HTTP-200-with-``state: failure`` envelope
that the portal uses for every rejection, the re-login-once path (there is no
refresh route), 429 handling, and the three-direction inbox unwrap.
"""
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.dpd.const import DpdApiError, DpdAuthError
from custom_components.dpd.countries.at.session import DpdAtSession

pytestmark = pytest.mark.asyncio


def _mock_response(status: int, body: object) -> MagicMock:
    response = MagicMock()
    response.status = status
    response.json = AsyncMock(return_value=body)
    return response


def _mock_session(*responses: MagicMock) -> MagicMock:
    queue = list(responses)
    # Only the jws.php POSTs. The priming GET is tracked apart so adding it
    # does not shift every index these tests assert on.
    calls: list[tuple] = []
    get_calls: list[tuple] = []

    @asynccontextmanager
    async def _ctx(url, **kwargs):
        calls.append((url, kwargs))
        yield queue.pop(0)

    @asynccontextmanager
    async def _get_ctx(url, **kwargs):
        get_calls.append((url, kwargs))
        page = MagicMock()
        page.status = 200
        page.read = AsyncMock(return_value=b"<html></html>")
        yield page

    session = MagicMock()
    session.post = MagicMock(side_effect=_ctx)
    session.get = MagicMock(side_effect=_get_ctx)
    session.calls = calls
    session.get_calls = get_calls
    return session


def _ok(payload: object, token: str = "jwt-next") -> MagicMock:
    """A data-call response, in the portal's real nested shape.

    No top-level ``state``: the token is lifted out, ``data`` is then the only
    key left, and the real envelope sits one level down. Confirmed from a live
    account on 2026-10-10.
    """
    return _mock_response(
        200, {"token": token, "data": {"state": "success", "data": payload}}
    )


def _inbox(inc=None, send=None, ret=None) -> MagicMock:
    return _ok(
        {
            "inc": {"data": inc if inc is not None else [], "max": 0},
            "send": {"data": send if send is not None else [], "max": 0},
            "ret": {"data": ret if ret is not None else [], "max": 0},
        }
    )


def _login_ok() -> MagicMock:
    return _mock_response(200, {"state": "success", "token": "jwt-1", "data": {}})


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


async def test_login_sends_positional_credentials_and_keeps_the_token():
    session = _mock_session(_login_ok(), _inbox())
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()

    url, kwargs = session.calls[0]
    assert url.endswith("/jws.php/usr/login")
    assert kwargs["json"] == ["user@example.test", "secret"]
    assert "Authorization" not in kwargs["headers"]

    await at.async_get_parcels()
    assert session.calls[1][1]["headers"]["Authorization"] == "Bearer jwt-1"


async def test_a_rejected_login_is_an_auth_error_despite_http_200():
    """The portal answers a wrong password with 200 + ``state: failure``."""
    session = _mock_session(_mock_response(200, {"state": "failure"}))
    with pytest.raises(DpdAuthError):
        await DpdAtSession(session, "user@example.test", "wrong").async_login()


async def test_a_login_accepted_without_a_token_is_a_shape_error_not_an_auth_one():
    """The credentials were accepted; a missing token must not force reauth."""
    session = _mock_session(_mock_response(200, {"state": "success", "data": {}}))
    with pytest.raises(DpdApiError):
        await DpdAtSession(session, "user@example.test", "secret").async_login()


async def test_a_non_dict_body_is_an_api_error():
    session = _mock_session(_mock_response(200, ["unexpected"]))
    with pytest.raises(DpdApiError):
        await DpdAtSession(session, "user@example.test", "secret").async_login()


async def test_a_401_is_an_auth_error():
    session = _mock_session(_mock_response(401, {}))
    with pytest.raises(DpdAuthError):
        await DpdAtSession(session, "user@example.test", "secret").async_login()


async def test_a_403_is_transient_not_an_auth_error():
    """403 is the host's bot protection, not a credential problem.

    Mapping it to auth would send a user with working credentials into
    reauth every time the protection tripped.
    """
    session = _mock_session(_mock_response(403, {}))
    with pytest.raises(DpdApiError) as err:
        await DpdAtSession(session, "user@example.test", "secret").async_login()
    assert err.value.status_code == 403


async def test_a_rate_limited_call_is_an_api_error_not_an_auth_error():
    """429 must surface as transient, never push the user into reauth."""
    session = _mock_session(_mock_response(429, {}))
    with pytest.raises(DpdApiError) as err:
        await DpdAtSession(session, "user@example.test", "secret").async_login()
    assert err.value.status_code == 429


async def test_a_server_error_is_an_api_error():
    session = _mock_session(_mock_response(503, {}))
    with pytest.raises(DpdApiError) as err:
        await DpdAtSession(session, "user@example.test", "secret").async_login()
    assert err.value.status_code == 503


async def test_the_inbox_logs_in_on_demand_when_no_token_is_held():
    """Setup always logs in first, but the inbox does not assume it."""
    session = _mock_session(_login_ok(), _inbox(inc=[{"parcelno": "1"}]))
    inbox = await DpdAtSession(
        session, "user@example.test", "secret"
    ).async_get_parcels()
    assert inbox["inc"] == [{"parcelno": "1"}]
    assert [url.rsplit("/", 1)[-1] for url, _ in session.calls] == [
        "login",
        "loadList",
    ]


async def test_an_on_demand_login_that_fails_raises_auth_error():
    session = _mock_session(_mock_response(200, {"state": "failure"}))
    with pytest.raises(DpdAuthError):
        await DpdAtSession(session, "user@example.test", "secret").async_get_parcels()


# --------------------------------------------------------------------------
# Re-login — there is no refresh route
# --------------------------------------------------------------------------


async def test_an_expired_token_triggers_exactly_one_relogin():
    session = _mock_session(
        _login_ok(),
        _mock_response(401, {}),  # the inbox call, token expired
        _mock_response(200, {"state": "success", "token": "jwt-2", "data": {}}),
        _inbox(inc=[{"parcelno": "1"}]),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    inbox = await at.async_get_parcels()

    assert inbox["inc"] == [{"parcelno": "1"}]
    assert [url.rsplit("/", 1)[-1] for url, _ in session.calls] == [
        "login",
        "loadList",
        "login",
        "loadList",
    ]
    assert session.calls[-1][1]["headers"]["Authorization"] == "Bearer jwt-2"


async def test_a_second_rejection_after_relogin_raises_auth_error():
    session = _mock_session(
        _login_ok(),
        _mock_response(401, {}),
        _login_ok(),
        _mock_response(401, {}),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    with pytest.raises(DpdAuthError):
        await at.async_get_parcels()


async def test_a_failed_relogin_raises_auth_error():
    session = _mock_session(
        _login_ok(),
        _mock_response(401, {}),
        _mock_response(200, {"state": "failure"}),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    with pytest.raises(DpdAuthError):
        await at.async_get_parcels()


# --------------------------------------------------------------------------
# The inbox unwrap
# --------------------------------------------------------------------------


async def test_the_inbox_returns_all_three_directions_from_one_call():
    session = _mock_session(
        _login_ok(),
        _inbox(inc=[{"parcelno": "1"}], send=[{"parcelno": "2"}], ret=[{"parcelno": "3"}]),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    inbox = await at.async_get_parcels()

    assert inbox == {
        "inc": [{"parcelno": "1"}],
        "send": [{"parcelno": "2"}],
        "ret": [{"parcelno": "3"}],
    }
    assert session.calls[1][1]["json"] == [None, None, None]


async def test_a_missing_direction_becomes_an_empty_list():
    session = _mock_session(_login_ok(), _ok({"inc": {"data": [{"parcelno": "1"}]}}))
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    assert await at.async_get_parcels() == {
        "inc": [{"parcelno": "1"}],
        "send": [],
        "ret": [],
    }


async def test_non_dict_entries_inside_a_direction_are_dropped():
    session = _mock_session(_login_ok(), _inbox(inc=[{"parcelno": "1"}, "junk", None]))
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    assert (await at.async_get_parcels())["inc"] == [{"parcelno": "1"}]


async def test_a_direction_of_the_wrong_shape_becomes_an_empty_list():
    session = _mock_session(_login_ok(), _ok({"inc": "nope", "send": {"data": "nope"}}))
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    assert await at.async_get_parcels() == {"inc": [], "send": [], "ret": []}


async def test_an_inbox_without_a_data_object_is_an_api_error():
    session = _mock_session(_login_ok(), _mock_response(200, {"state": "success"}))
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    with pytest.raises(DpdApiError):
        await at.async_get_parcels()


# --------------------------------------------------------------------------
# Application-level refusal vs auth failure
# --------------------------------------------------------------------------


async def test_a_refused_data_call_is_not_an_auth_failure(caplog):
    """A refusal after a good login must retry, never demand reauth.

    Pushing the user into reauth here would be wrong — the credentials just
    worked. The envelope's keys are logged so the real cause is diagnosable.
    """
    session = _mock_session(
        _login_ok(),
        _mock_response(200, {"state": "failure", "message": "nope"}),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()

    with pytest.raises(DpdApiError):
        await at.async_get_parcels()

    assert "refused parcel/loadList after a successful login" in caplog.text
    assert "'message'" in caplog.text  # keys reported...
    assert "nope" not in caplog.text  # ...values never


async def test_a_refused_data_call_does_not_trigger_a_relogin():
    session = _mock_session(_login_ok(), _mock_response(200, {"state": "failure"}))
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    with pytest.raises(DpdApiError):
        await at.async_get_parcels()

    assert [url.rsplit("/", 1)[-1] for url, _ in session.calls] == [
        "login",
        "loadList",
    ]


async def test_the_portal_is_primed_once_before_the_first_login():
    """A browser reaches jws.php only after loading a page; so does this."""
    session = _mock_session(_login_ok(), _inbox(), _login_ok())
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    await at.async_get_parcels()
    await at.async_login()

    assert session.get.call_count == 1


async def test_a_portal_that_cannot_be_primed_still_attempts_the_login():
    """A changed landing page must not break setup by itself."""
    import aiohttp

    session = _mock_session(_login_ok(), _inbox())
    session.get = MagicMock(side_effect=aiohttp.ClientError("boom"))
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    assert (await at.async_get_parcels())["inc"] == []


# --------------------------------------------------------------------------
# The nested envelope and token rotation — the two live bugs of 2026-10-10
# --------------------------------------------------------------------------


async def test_a_data_call_envelope_is_descended_into():
    """Regression: a data call has no top-level ``state``.

    It answers ``{token, data: {state, data}}``. Reading ``state`` at the top
    level found ``None`` and refused every poll, while login — which answers
    ``{state, token, data}`` — worked. That asymmetry is the whole bug.
    """
    session = _mock_session(
        _login_ok(),
        _mock_response(
            200,
            {
                "token": "jwt-2",
                "data": {
                    "state": "success",
                    "data": {"inc": {"data": [{"parcelno": "1"}], "max": 1}},
                },
            },
        ),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    assert (await at.async_get_parcels())["inc"] == [{"parcelno": "1"}]


async def test_the_rotated_token_is_used_on_the_next_call():
    """Every response may carry a fresh token; keeping a stale one expires."""
    session = _mock_session(
        _login_ok(),
        _ok({"inc": {"data": [], "max": 0}}, token="jwt-rotated"),
        _ok({"inc": {"data": [], "max": 0}}, token="jwt-rotated-again"),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()

    await at.async_get_parcels()
    assert session.calls[1][1]["headers"]["Authorization"] == "Bearer jwt-1"

    await at.async_get_parcels()
    assert session.calls[2][1]["headers"]["Authorization"] == "Bearer jwt-rotated"


async def test_a_login_envelope_keeps_its_two_keys_and_is_not_descended_into():
    session = _mock_session(_login_ok(), _inbox())
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    assert (await at.async_get_parcels())["inc"] == []


async def test_a_token_only_response_without_data_is_still_refused():
    session = _mock_session(_login_ok(), _mock_response(200, {"token": "jwt-2"}))
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    with pytest.raises(DpdApiError):
        await at.async_get_parcels()


async def test_a_non_string_token_does_not_replace_a_working_one():
    session = _mock_session(
        _login_ok(),
        _mock_response(
            200,
            {"token": None, "data": {"state": "success", "data": {}}},
        ),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    await at.async_get_parcels()
    assert session.calls[1][1]["headers"]["Authorization"] == "Bearer jwt-1"


async def test_a_nested_envelope_without_a_token_is_still_descended_into():
    """Robustness: a response omitting the rotated token must still read."""
    session = _mock_session(
        _login_ok(),
        _mock_response(
            200, {"data": {"state": "success", "data": {"inc": {"data": []}}}}
        ),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    assert (await at.async_get_parcels())["inc"] == []


async def test_a_persisting_refusal_warns_once_not_once_per_poll(caplog):
    session = _mock_session(
        _login_ok(),
        _mock_response(200, {"state": "failure"}),
        _mock_response(200, {"state": "failure"}),
    )
    at = DpdAtSession(session, "user@example.test", "secret")
    await at.async_login()
    for _ in range(2):
        with pytest.raises(DpdApiError):
            await at.async_get_parcels()

    assert caplog.text.count("refused parcel/loadList") == 1
