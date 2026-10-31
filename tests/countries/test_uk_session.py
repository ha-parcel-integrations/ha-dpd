"""Tests for DPD UK's Firebase magic-link session and parcel lists."""

import base64
from contextlib import asynccontextmanager
import json
import logging
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

import aiohttp
import pytest

from custom_components.dpd.const import (
    DPD_UK_FIREBASE_API_KEY,
    DPD_UK_WEB_FIREBASE_API_KEY,
    DpdApiError,
    DpdAuthError,
)
from custom_components.dpd.countries.uk.session import (
    DpdUkEmailRequiredError,
    DpdUkLinkError,
    DpdUkNotConsumerError,
    DpdUkSession,
    DpdUkUnrecognisedLinkError,
    classify_link,
    decode_claims,
)

INNER = "https://example.invalid/action?apiKey=k&mode=signIn&oobCode=abc123"
WRAPPED = f"https://example.invalid/__/auth/links?link={quote(INNER, safe='')}&x=1"


def jwt(claims: dict) -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'none'})}.{part(claims)}.sig"


WITH_CLAIM = jwt({"consumerId": "c-1", "phone_number": "+440000000000"})
NO_CLAIM = jwt({"sub": "u"})


def resp(status: int, body: object = None, *, bad_json: bool = False) -> MagicMock:
    r = MagicMock()
    r.status = status
    if bad_json:
        r.json = AsyncMock(side_effect=ValueError("not json"))
    else:
        r.json = AsyncMock(return_value=body)
    return r


def firebase(id_token: str, refresh: str = "rt", **extra) -> dict:
    return {"idToken": id_token, "refreshToken": refresh, "expiresIn": "3600", **extra}


class Fake:
    """Session whose answers are queued per URL fragment."""

    def __init__(self, routes: dict[str, list[MagicMock]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        for fragment, queue in self.routes.items():
            if fragment in url:
                response = queue.pop(0)
                break
        else:  # pragma: no cover
            raise AssertionError(f"unrouted {url}")

        @asynccontextmanager
        async def ctx():
            yield response

        return ctx()

    def count(self, fragment: str) -> int:
        return sum(fragment in url for _, url, _ in self.calls)


def securetoken(id_token: str = WITH_CLAIM, refresh: str = "rt2") -> dict:
    return {"id_token": id_token, "refresh_token": refresh, "expires_in": "3600"}


REVOKED = resp(400, {"error": {"message": "TOKEN_EXPIRED"}})


def sign_in_routes(**over) -> dict[str, list[MagicMock]]:
    routes = {
        "verifyMagicLink": [resp(200, {"data": {"customToken": "ct1"}})],
        "signInWithCustomToken": [
            resp(200, firebase(NO_CLAIM, "rt1")),
            resp(200, firebase(WITH_CLAIM, "rt2")),
        ],
        "refreshUserClaims": [resp(200, {"data": {"customToken": "ct2"}})],
        "securetoken": [resp(200, securetoken())],
        "device/login": [resp(200, {"data": {"dpdSession": "ds1"}})],
    }
    routes.update(over)
    return routes


WEB_LINK = "https://login.dpd.co.uk/signin?verificationId=vid-123&x=1"


def test_classify_link_routes_by_shape():
    assert classify_link(WRAPPED) == ("app", INNER)
    assert classify_link(f"  {INNER} ") == ("app", INNER)
    assert classify_link(WEB_LINK) == ("web", "vid-123")


@pytest.mark.parametrize(
    "link",
    [
        "not a link",
        "https://example.invalid/page",
        "ftp:///x",
        "https://a.invalid/?verificationId=",
    ],
)
def test_classify_link_rejects_unrecognised_links(link):
    with pytest.raises(DpdUkUnrecognisedLinkError):
        classify_link(link)


def test_decode_claims_survives_garbage():
    assert decode_claims("nope") == {}
    assert decode_claims("a.!!!.c") == {}
    assert decode_claims(jwt({"consumerId": "c"}))["consumerId"] == "c"
    payload = base64.urlsafe_b64encode(b"[1]").decode()
    assert decode_claims(f"a.{payload}.c") == {}


async def test_sign_in_runs_the_whole_chain_and_keeps_only_refresh_material():
    saved = []
    fake = Fake(sign_in_routes())
    uk = DpdUkSession(fake, None, "dev", saved.append)

    consumer = await uk.async_sign_in_with_link("me@example.invalid", WRAPPED)

    assert consumer == "c-1"
    assert uk.refresh_token == "rt2"
    assert saved == ["rt1", "rt2"]
    verify = fake.calls[0]
    assert verify[2]["params"] == {"email": "me@example.invalid", "link": INNER}
    device = next(c for c in fake.calls if "device/login" in c[1])
    assert device[2]["json"] == {
        "consumerId": "c-1",
        "deviceId": "dev",
        "sms": "+440000000000",
    }
    assert device[2]["headers"]["Authorization"] == f"Bearer {WITH_CLAIM}"
    sign_in = next(c for c in fake.calls if "signInWithCustomToken" in c[1])
    assert sign_in[2]["headers"] == {"X-Android-Package": "com.dpd.yourdpd"}


@pytest.mark.parametrize("body", [{"error": "EXPIRED_OOB_CODE"}, "INVALID_OOB_CODE"])
async def test_sign_in_reports_an_expired_link(body):
    fake = Fake({"verifyMagicLink": [resp(400, body)]})
    with pytest.raises(DpdUkLinkError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)


async def test_sign_in_rejected_link_is_an_auth_error():
    fake = Fake({"verifyMagicLink": [resp(404, {})]})
    with pytest.raises(DpdAuthError) as err:
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)
    assert not isinstance(err.value, DpdUkLinkError)


@pytest.mark.parametrize(
    ("verify", "match"),
    [(resp(500, {}), 500), (resp(200, {"data": {}}), 200)],
)
async def test_sign_in_broker_failure_is_an_api_error(verify, match):
    fake = Fake({"verifyMagicLink": [verify]})
    with pytest.raises(DpdApiError) as err:
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)
    assert err.value.status_code == match


async def test_sign_in_without_consumer_claim_is_not_a_consumer():
    fake = Fake(
        sign_in_routes(
            signInWithCustomToken=[
                resp(200, firebase(NO_CLAIM)),
                resp(200, firebase(NO_CLAIM)),
            ]
        )
    )
    with pytest.raises(DpdUkNotConsumerError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)
    assert fake.count("device/login") == 0


@pytest.mark.parametrize("status", [400, 403])
async def test_firebase_rejecting_the_custom_token_is_an_auth_error(status):
    fake = Fake(
        sign_in_routes(signInWithCustomToken=[resp(status, {"error": "INVALID"})])
    )
    with pytest.raises(DpdAuthError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)


@pytest.mark.parametrize(
    "answer", [resp(500, {}), resp(200, ["x"]), resp(200, {"nothing": 1})]
)
async def test_firebase_odd_answers(answer):
    fake = Fake(sign_in_routes(signInWithCustomToken=[answer]))
    with pytest.raises((DpdApiError, DpdAuthError)):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)


async def test_claims_upgrade_refusals():
    fake = Fake(sign_in_routes(refreshUserClaims=[resp(403, {})]))
    fake.routes["signInWithCustomToken"] = fake.routes["signInWithCustomToken"][:1]
    with pytest.raises(DpdAuthError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)

    fake = Fake(sign_in_routes(refreshUserClaims=[resp(200, {"data": {}})]))
    fake.routes["signInWithCustomToken"] = fake.routes["signInWithCustomToken"][:1]
    with pytest.raises(DpdApiError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)


async def test_sign_in_never_stores_a_token_that_is_dead_on_arrival(monkeypatch):
    monkeypatch.setattr(
        "custom_components.dpd.countries.uk.session.asyncio.sleep", AsyncMock()
    )
    saved = []
    fake = Fake(
        sign_in_routes(
            signInWithCustomToken=[
                resp(200, firebase(NO_CLAIM, "rt1")),
                resp(200, firebase(WITH_CLAIM, "rt2")),
                resp(200, firebase(WITH_CLAIM, "rt3")),
            ],
            refreshUserClaims=[
                resp(200, {"data": {"customToken": "ct2"}}),
                resp(200, {"data": {"customToken": "ct3"}}),
            ],
            securetoken=[REVOKED, resp(200, securetoken(refresh="rt4"))],
        )
    )
    uk = DpdUkSession(fake, None, "dev", saved.append)

    assert await uk.async_sign_in_with_link("a@b.c", INNER) == "c-1"
    assert uk.refresh_token == "rt4"
    assert saved[-1] == "rt4"
    assert fake.count("refreshUserClaims") == 2


async def test_sign_in_fails_when_the_retried_token_is_dead_too(monkeypatch):
    monkeypatch.setattr(
        "custom_components.dpd.countries.uk.session.asyncio.sleep", AsyncMock()
    )
    fake = Fake(
        sign_in_routes(
            signInWithCustomToken=[
                resp(200, firebase(NO_CLAIM, "rt1")),
                resp(200, firebase(WITH_CLAIM, "rt2")),
                resp(200, firebase(WITH_CLAIM, "rt3")),
            ],
            refreshUserClaims=[
                resp(200, {"data": {"customToken": "ct2"}}),
                resp(200, {"data": {"customToken": "ct3"}}),
            ],
            securetoken=[REVOKED, REVOKED],
        )
    )
    with pytest.raises(DpdAuthError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)
    assert fake.count("device/login") == 0


async def test_a_website_sign_in_does_not_retry_the_claims_upgrade():
    fake = Fake(
        sign_in_routes(
            signInWithCustomToken=[resp(200, firebase(WITH_CLAIM, "rt1"))],
            securetoken=[REVOKED],
        )
    )
    with pytest.raises(DpdAuthError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("", WEB_LINK)
    assert fake.count("refreshUserClaims") == 0


@pytest.mark.parametrize(
    ("answer", "error"),
    [
        (resp(403, {}), DpdAuthError),
        (resp(500, {}), DpdApiError),
        (resp(200, {"data": {}}), DpdApiError),
    ],
)
async def test_device_login_failures(answer, error):
    fake = Fake(sign_in_routes(**{"device/login": [answer]}))
    with pytest.raises(error):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", INNER)


async def test_device_login_without_a_phone_claim_sends_an_empty_string():
    fake = Fake(
        {
            "securetoken": [resp(200, {"id_token": jwt({"consumerId": "c-1"})})],
            "device/login": [resp(200, {"data": {"dpdSession": "ds"}})],
        }
    )
    await DpdUkSession(fake, "rt", "dev").async_login()
    assert fake.calls[1][2]["json"]["sms"] == ""


def logged_in_routes(**over):
    routes = {
        "securetoken": [resp(200, firebase(WITH_CLAIM, "rt-new"))],
        "device/login": [resp(200, {"data": {"dpdSession": "ds1"}})],
        "parcels/active": [resp(200, {"data": []})],
        "parcels/inactive": [resp(200, {"data": []})],
    }
    routes.update(over)
    return routes


async def test_login_refreshes_the_token_and_empty_lists_are_fine():
    saved = []
    fake = Fake(logged_in_routes())
    uk = DpdUkSession(fake, "rt", "dev", saved.append)

    await uk.async_login()
    active, inactive = await uk.async_get_parcels()

    assert (active, inactive) == ([], [])
    assert saved == ["rt-new"]
    assert uk.consumer_id == "c-1"
    listing = fake.calls[-1]
    assert listing[1].endswith("/v1.5/consumers/c-1/parcels/inactive")
    assert listing[2]["params"] == {"userType": "R"}
    assert listing[2]["headers"]["dpdSession"] == "ds1"
    assert fake.count("securetoken") == 1


async def test_list_filters_non_dict_items():
    fake = Fake(
        logged_in_routes(**{"parcels/active": [resp(200, {"data": [{"a": 1}, "x"]})]})
    )
    uk = DpdUkSession(fake, "rt", "dev")
    active, _ = await uk.async_get_parcels()
    assert active == [{"a": 1}]
    # the dpd session was minted lazily on the first list call
    assert fake.count("device/login") == 1


async def test_a_stale_id_token_is_refreshed_before_use():
    fake = Fake(
        logged_in_routes(
            securetoken=[
                resp(200, firebase(WITH_CLAIM, expiresIn="10")),
                resp(200, firebase(WITH_CLAIM)),
            ]
        )
    )
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    await uk.async_get_parcels()
    assert fake.count("securetoken") == 2


async def test_session_invalid_remints_once_and_retries():
    fake = Fake(
        logged_in_routes(
            **{
                "device/login": [
                    resp(200, {"data": {"dpdSession": "ds1"}}),
                    resp(200, {"data": {"dpdSession": "ds2"}}),
                ],
                "parcels/active": [
                    resp(403, {"message": "Your session is invalid"}),
                    resp(200, {"data": []}),
                ],
            }
        )
    )
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    await uk.async_get_parcels()
    actives = [c for c in fake.calls if c[1].endswith("parcels/active")]
    assert [c[2]["headers"]["dpdSession"] for c in actives] == ["ds1", "ds2"]


async def test_session_invalid_twice_is_a_visible_failure(caplog):
    fake = Fake(
        logged_in_routes(
            **{
                "device/login": [
                    resp(200, {"data": {"dpdSession": "ds1"}}),
                    resp(200, {"data": {"dpdSession": "ds2"}}),
                ],
                "parcels/active": [resp(403, "Your session is invalid")] * 2,
            }
        )
    )
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    with caplog.at_level(logging.WARNING), pytest.raises(DpdApiError):
        await uk.async_get_parcels()
    assert "refused or garbled" in caplog.text
    assert "ds1" not in caplog.text


async def test_other_403_warns_once_and_fails():
    fake = Fake(
        logged_in_routes(
            **{"parcels/active": [resp(403, {"message": "not authorized"})] * 2}
        )
    )
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    for _ in range(2):
        with pytest.raises(DpdApiError):
            await uk.async_get_parcels()


async def test_401_refreshes_the_id_token_once_then_fails_auth():
    fake = Fake(
        logged_in_routes(
            securetoken=[resp(200, firebase(WITH_CLAIM))] * 2,
            **{"parcels/active": [resp(401, {})] * 2},
        )
    )
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    with pytest.raises(DpdAuthError):
        await uk.async_get_parcels()
    assert fake.count("securetoken") == 2


async def test_401_then_success_after_refresh():
    fake = Fake(
        logged_in_routes(
            securetoken=[resp(200, firebase(WITH_CLAIM))] * 2,
            **{"parcels/active": [resp(401, {}), resp(200, {"data": []})]},
        )
    )
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    await uk.async_get_parcels()


@pytest.mark.parametrize(
    "answer", [resp(500, {}), resp(200, {"data": {"not": "a list"}}), resp(200, None)]
)
async def test_list_errors_are_never_a_silent_empty_inbox(answer):
    fake = Fake(logged_in_routes(**{"parcels/active": [answer]}))
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    with pytest.raises(DpdApiError):
        await uk.async_get_parcels()


async def test_non_json_body_is_tolerated():
    fake = Fake(
        logged_in_routes(**{"parcels/active": [resp(502, None, bad_json=True)]})
    )
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    with pytest.raises(DpdApiError) as err:
        await uk.async_get_parcels()
    assert err.value.status_code == 502


async def test_refresh_without_a_token_or_with_a_rejected_one_needs_reauth():
    with pytest.raises(DpdAuthError):
        await DpdUkSession(Fake({}), None, "dev").async_login()
    fake = Fake({"securetoken": [resp(400, {"error": {"message": "TOKEN_EXPIRED"}})]})
    with pytest.raises(DpdAuthError):
        await DpdUkSession(fake, "rt", "dev").async_login()


@pytest.mark.parametrize(
    "answer", [resp(400, {"error": "SOMETHING_ELSE"}), resp(503, {}), resp(200, [])]
)
async def test_an_unconfirmed_refresh_failure_never_reaches_reauth(answer):
    fake = Fake({"securetoken": [answer]})
    with pytest.raises(DpdApiError):
        await DpdUkSession(fake, "rt", "dev").async_login()


async def test_an_existing_account_without_claims_is_upgraded_on_login():
    fake = Fake(
        {
            "securetoken": [resp(200, firebase(NO_CLAIM))],
            "refreshUserClaims": [resp(200, {"data": {"customToken": "ct"}})],
            "signInWithCustomToken": [resp(200, firebase(WITH_CLAIM, "rt9"))],
            "device/login": [resp(200, {"data": {"dpdSession": "ds"}})],
        }
    )
    uk = DpdUkSession(fake, "rt", "dev")
    await uk.async_login()
    assert uk.consumer_id == "c-1"


async def test_login_without_a_consumer_profile_is_not_a_consumer():
    fake = Fake(
        {
            "securetoken": [resp(200, firebase(NO_CLAIM))],
            "refreshUserClaims": [resp(200, {"data": {"customToken": "ct"}})],
            "signInWithCustomToken": [resp(200, firebase(NO_CLAIM))],
        }
    )
    with pytest.raises(DpdUkNotConsumerError):
        await DpdUkSession(fake, "rt", "dev").async_login()


async def test_transport_errors_propagate():
    session = MagicMock()
    session.request.side_effect = aiohttp.ClientError()
    with pytest.raises(aiohttp.ClientError):
        await DpdUkSession(session, "rt", "dev").async_login()


def test_link_and_consumer_errors_are_auth_errors():
    assert issubclass(DpdUkLinkError, DpdAuthError)
    assert issubclass(DpdUkNotConsumerError, DpdAuthError)


async def test_website_link_signs_in_without_email_or_claims_round():
    saved = []
    fake = Fake(
        {
            "verifyMagicLink": [resp(200, {"data": {"customToken": "ct"}})],
            "signInWithCustomToken": [resp(200, firebase(WITH_CLAIM, "rt-web"))],
            "securetoken": [resp(200, securetoken(refresh="rt-web"))],
            "device/login": [resp(200, {"data": {"dpdSession": "ds1"}})],
        }
    )
    uk = DpdUkSession(fake, None, "dev", saved.append)

    consumer = await uk.async_sign_in_with_link("", WEB_LINK)

    assert consumer == "c-1"
    assert uk.source == "web"
    assert fake.count("refreshUserClaims") == 0
    verify = fake.calls[0]
    assert verify[1] == "https://apis.login.dpd.co.uk/auth/verifyMagicLink"
    assert verify[2]["params"] == {"verificationId": "vid-123"}
    sign_in = fake.calls[1]
    assert sign_in[2]["params"] == {"key": DPD_UK_WEB_FIREBASE_API_KEY}
    assert sign_in[2]["headers"] == {}
    assert DPD_UK_WEB_FIREBASE_API_KEY != DPD_UK_FIREBASE_API_KEY
    assert saved == ["rt-web"]


async def test_app_link_uses_the_app_key_and_the_consumers_host():
    fake = Fake(sign_in_routes())
    await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", WRAPPED)
    assert fake.calls[0][1].startswith("https://apis.consumers.dpdgroup.co.uk/v1/")
    sign_in = next(c for c in fake.calls if "signInWithCustomToken" in c[1])
    assert sign_in[2]["params"] == {"key": DPD_UK_FIREBASE_API_KEY}


async def test_app_link_without_email_is_refused_before_any_request():
    fake = Fake({})
    with pytest.raises(DpdUkEmailRequiredError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("  ", WRAPPED)
    assert fake.calls == []


async def test_unrecognised_link_is_refused_before_any_request():
    fake = Fake({})
    with pytest.raises(DpdUkUnrecognisedLinkError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("a@b.c", "hello")
    assert fake.calls == []


@pytest.mark.parametrize("status", [400, 404, 410])
async def test_website_link_refusal_means_spent_link(status):
    fake = Fake({"verifyMagicLink": [resp(status, {})]})
    with pytest.raises(DpdUkLinkError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("", WEB_LINK)


@pytest.mark.parametrize("answer", [resp(500, {}), resp(200, {"data": {}})])
async def test_website_broker_failure_is_an_api_error(answer):
    fake = Fake({"verifyMagicLink": [answer]})
    with pytest.raises(DpdApiError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("", WEB_LINK)


async def test_website_token_without_a_consumer_claim_is_not_a_consumer():
    fake = Fake(
        {
            "verifyMagicLink": [resp(200, {"data": {"customToken": "ct"}})],
            "signInWithCustomToken": [resp(200, firebase(NO_CLAIM))],
        }
    )
    with pytest.raises(DpdUkNotConsumerError):
        await DpdUkSession(fake, None, "dev").async_sign_in_with_link("", WEB_LINK)


async def test_a_website_entry_refreshes_with_the_web_key_and_never_upgrades():
    fake = Fake({"securetoken": [resp(200, firebase(NO_CLAIM))]})
    uk = DpdUkSession(fake, "rt", "dev", source="web")
    with pytest.raises(DpdUkNotConsumerError):
        await uk.async_login()
    assert fake.calls[0][2]["params"] == {"key": DPD_UK_WEB_FIREBASE_API_KEY}
    assert fake.calls[0][2]["headers"] == {}
    assert fake.count("refreshUserClaims") == 0
