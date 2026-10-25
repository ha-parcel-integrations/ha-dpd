"""Tests for DPD Germany's web-account session, against a scripted fake site."""

import asyncio
from contextlib import asynccontextmanager
import logging
import re
from urllib.parse import urlparse

import pytest

from custom_components.dpd.const import DpdApiError, DpdAuthError, DpdCaptchaError
from custom_components.dpd.countries.de import session as de_session
from custom_components.dpd.countries.de.session import DpdDeSession
from tests.payloads import (
    DE_BARCODE,
    DE_BARCODE_2,
    de_inbox_page,
    de_login_page,
    de_portal_page,
    de_slots,
)

LOGIN = "https://www.dpd.com/de/de/mydpd-anmelden-und-registrieren/?lng=DEU"
PORTAL = "https://portal.dpd.de/home_pn.aspx"
INBOX = "https://my.dpd.de/myParcel.aspx"
PUBLIC_LOGIN = "https://www.dpd.com/de/de/mydpd-anmelden-und-registrieren/"
EMAIL = "user@example.de"
PASSWORD = "not-a-real-password"


class FakeResponse:
    def __init__(self, status=200, body="", headers=None, content_type="text/html"):
        self.status = status
        self._body = body
        self.headers = headers or {}
        self.content_type = content_type

    async def text(self, errors="strict"):
        return self._body


def redirect(location, status=302):
    return FakeResponse(status, headers={"Location": location})


class FakeDpd:
    """A scripted stand-in for the three hosts of the web account.

    It enforces what the real site does: a postback is only answered with a
    page when it carries the form state of the page served just before.
    """

    def __init__(self, tabs=None, details=None):
        self.tabs = {"incoming": [], "sending": [], "returning": []}
        self.tabs.update(tabs or {})
        self.details = details or {}
        self.portal_session = False
        self.my_session = False
        self.login_mode = "ok"
        self.hop_works = True
        self.portal_in_place = False
        self.counters_hidden = False
        self.select_mode = {}
        self.calls = []
        self.served = 0
        self.expected_state = None
        self.yield_on_request = False

    # -- helpers for the tests
    def selects(self):
        return [
            c for c in self.calls if c["target"] and "btnSelectParcel" in c["target"]
        ]

    def logins(self):
        return [c for c in self.calls if c["url"] == LOGIN]

    def hops(self):
        return [
            c
            for c in self.calls
            if c["target"] == "ctl00$cphBody$ctl00$btnHomeMyParcel"
        ]

    def expire_my(self):
        self.my_session = False

    def expire_all(self):
        self.my_session = False
        self.portal_session = False

    # -- the aiohttp surface
    @asynccontextmanager
    async def request(
        self, method, url, *, data=None, headers=None, allow_redirects=True
    ):
        assert allow_redirects is False
        if self.yield_on_request:
            await asyncio.sleep(0)
        self.calls.append(
            {
                "method": method,
                "url": url,
                "data": data,
                "headers": headers or {},
                "target": (data or {}).get("__EVENTTARGET"),
            }
        )
        yield self._respond(method, url, data or {})

    # -- the site
    def _respond(self, method, url, data):
        parsed = urlparse(url)
        if url == LOGIN:
            return self._login(data)
        if parsed.hostname == "www.dpd.com":
            return FakeResponse(200, de_portal_page(signed_in=False))
        if parsed.hostname == "portal.dpd.de":
            return self._portal(method, parsed, data)
        if parsed.hostname == "my.dpd.de":
            return self._my(method, parsed, data)
        return FakeResponse(404)  # pragma: no cover

    def _login(self, data):
        assert set(data) == {"dpg_username", "dpg_password"}
        mode = self.login_mode
        if mode == "ok":
            self.portal_session = True
            return redirect("https://portal.dpd.de/home.aspx?dpd_token=abc123")
        if mode == "no_token":
            return redirect("https://portal.dpd.de/home.aspx")
        if mode == "other_host":
            return redirect("https://example.invalid/home.aspx?dpd_token=abc")
        if mode == "no_location":
            return FakeResponse(302)
        if mode == "captcha_form":
            return FakeResponse(200, de_login_page(form_class="dgs-form-has-captcha"))
        if mode == "captcha_sitekey":
            return FakeResponse(
                200, de_login_page(captcha='<div data-sitekey="k"></div>')
            )
        if mode == "captcha_script":
            return FakeResponse(
                200,
                de_login_page(
                    captcha='<script src="https://www.google.com/recaptcha/api.js"></script>'
                ),
            )
        if mode == "rejected":
            message = '<div class="dgs-inline-message"><p>Zugangsdaten falsch</p></div>'
            return FakeResponse(200, de_login_page(message=message))
        if mode == "rejected_field":
            return FakeResponse(
                200,
                de_login_page(
                    username_value="Login unsuccessful.",
                    username_placeholder="Login unsuccessful.",
                ),
            )
        if mode == "echoed_username":
            return FakeResponse(200, de_login_page(username_value=EMAIL))
        if mode == "form_no_message":
            return FakeResponse(200, de_login_page())
        if mode == "other_200":
            return FakeResponse(200, "<html>Wartungsarbeiten</html>")
        return FakeResponse(int(mode))

    def _portal(self, method, parsed, data):
        if parsed.path == "/home.aspx":
            return redirect("/home_pn.aspx")
        if method == "GET":
            if not self.portal_session and not self.portal_in_place:
                return redirect(PUBLIC_LOGIN)
            return FakeResponse(200, de_portal_page(signed_in=self.portal_session))
        assert data["__VIEWSTATE"] == "VS-P"
        if not self.portal_session or not self.hop_works:
            return FakeResponse(200, de_portal_page(signed_in=self.portal_session))
        self.my_session = True
        return redirect("https://my.dpd.de/myParcel.aspx?dpd_token=def456")

    def _my(self, method, parsed, data):
        if parsed.query:
            return redirect("/myParcel.aspx")
        if method == "GET":
            return FakeResponse(200, self._inbox("incoming"))
        if data["__VIEWSTATE"] != self.expected_state:
            return FakeResponse(200, "<html><title>Runtime Error</title></html>")
        tab = data["__VIEWSTATE"].rsplit("-", 1)[1]
        target = data["__EVENTTARGET"]
        for name in ("incoming", "sending", "returning"):
            if target.endswith(f"btnGet{name.capitalize()}Parcel"):
                return FakeResponse(200, self._inbox(name))
        index = int(re.search(r"repParcelList\$ctl(\d+)", target).group(1))
        row = self.tabs[tab][index]
        mode = self.select_mode.get(row["barcode"])
        if mode == "error":
            return FakeResponse(500)
        if mode == "signed_out":
            return FakeResponse(200, de_inbox_page(self.tabs[tab], signed_in=False))
        if mode == "no_panel":
            return FakeResponse(200, self._inbox(tab))
        detail = dict(self.details[row["barcode"]])
        if mode == "mismatch":
            detail["barcode"] = "99999999999999"
        return FakeResponse(200, self._inbox(tab, detail))

    def _inbox(self, tab, detail=None):
        self.served += 1
        token = f"{self.served}-{tab}"
        self.expected_state = f"VS-{token}"
        counts = tuple(
            len(self.tabs[name]) for name in ("incoming", "sending", "returning")
        )
        page = de_inbox_page(
            self.tabs[tab],
            token=token,
            counters=counts,
            detail=detail,
            signed_in=self.my_session,
        )
        if self.counters_hidden:
            page = re.sub(
                r'<span id="ContentPlaceHolder1_counterGet\w+".*?</span>', "", page
            )
        return page


def _row(barcode=DE_BARCODE, sentence="Paket unterwegs", icon=2):
    return {
        "barcode": barcode,
        "label": "DPD Paket",
        "sentence": sentence,
        "icon": icon,
    }


def _detail(barcode=DE_BARCODE, **kwargs):
    return {
        "barcode": barcode,
        "slots": de_slots(
            ("Paketinfo an DPD übergeben", "18.10."), ("Paket unterwegs", "19.10.")
        ),
        "weight": "10,95",
        "flow_type": "0",
        "icon": 2,
        **kwargs,
    }


def _site(**kwargs):
    site = FakeDpd(
        tabs={"incoming": [_row()]},
        details={DE_BARCODE: _detail()},
    )
    for key, value in kwargs.items():
        setattr(site, key, value)
    return site


def _session(site):
    return DpdDeSession(site, EMAIL, PASSWORD)


# -- login chain -----------------------------------------------------------


async def test_login_posts_credentials_then_the_hop_with_every_input():
    site = _site()
    await _session(site).async_login()

    assert [c["method"] for c in site.calls[:1]] == ["POST"]
    login = site.logins()[0]
    assert login["data"] == {"dpg_username": EMAIL, "dpg_password": PASSWORD}
    assert login["headers"]["Origin"] == "https://www.dpd.com"
    assert login["headers"]["Referer"] == LOGIN
    assert "Mozilla" in login["headers"]["User-Agent"]

    (hop,) = site.hops()
    assert hop["url"] == PORTAL
    assert hop["data"]["__EVENTARGUMENT"] == ""
    assert hop["data"]["__VIEWSTATE"] == "VS-P"
    assert hop["data"]["__VIEWSTATEGENERATOR"] == "GEN-P"
    assert hop["data"]["__EVENTVALIDATION"] == "EV-P"
    assert hop["data"]["ctl00$cphBody$ctl00$txtSearch"] == ""
    assert "ctl00$cphBody$ctl00$pw" not in hop["data"]
    assert hop["headers"]["Origin"] == "https://portal.dpd.de"
    assert hop["headers"]["Referer"] == PORTAL
    assert site.my_session


async def test_a_session_that_never_hops_would_see_an_empty_inbox():
    """The hop is what opens the parcel list; the fake renders it signed out."""
    site = _site()
    site.portal_session = True
    page = await _session(site)._fetch("GET", INBOX)
    assert "btnLogout" not in page


async def test_login_follows_redirects_without_forwarding_the_form_headers():
    site = _site()
    await _session(site).async_login()
    follow = [c for c in site.calls if c["method"] == "GET"]
    assert follow
    assert all("Origin" not in c["headers"] for c in follow)


# -- ladder ----------------------------------------------------------------


async def test_a_live_session_skips_the_login():
    site = _site()
    session = _session(site)
    await session.async_login()
    site.calls.clear()
    inbox = await session.async_get_inbox()
    assert site.logins() == [] and site.hops() == []
    assert inbox["incoming"][0]["barcode"] == DE_BARCODE


async def test_rung2_hops_again_when_only_the_parcel_host_session_is_gone():
    site = _site()
    session = _session(site)
    await session.async_login()
    site.expire_my()
    site.calls.clear()
    await session.async_get_inbox()
    assert site.logins() == []
    assert len(site.hops()) == 1


async def test_rung3_logs_in_only_when_both_sessions_are_gone():
    site = _site()
    session = _session(site)
    await session.async_login()
    site.expire_all()
    site.calls.clear()
    inbox = await session.async_get_inbox()
    assert len(site.logins()) == 1
    assert inbox["incoming"]


async def test_a_login_that_opens_nothing_fails_once_then_backs_off_for_an_hour(
    monkeypatch, caplog
):
    clock = [1000.0]
    monkeypatch.setattr(de_session.time, "monotonic", lambda: clock[0])
    site = _site(hop_works=False)
    session = _session(site)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(DpdApiError):
            await session.async_get_inbox()
        assert len(site.logins()) == 1

        # Within the hour: the cheap rungs run, but no second login.
        clock[0] += 1800
        with pytest.raises(DpdApiError) as err:
            await session.async_get_inbox()
        assert type(err.value) is DpdApiError
        assert len(site.logins()) == 1

        # After the hour a login is tried again, and a working one clears it.
        clock[0] += 3600
        site.hop_works = True
        site.expire_all()
        assert (await session.async_get_inbox())["incoming"]
    assert len(site.logins()) == 2
    assert len([r for r in caplog.records if "did not open" in r.getMessage()]) == 1
    assert not de_session._UNOPENED_UNTIL


async def test_a_signed_out_parcel_list_after_a_login_is_not_retried_in_the_poll():
    site = _site(hop_works=False)
    with pytest.raises(DpdApiError):
        await _session(site).async_get_inbox()
    assert len(site.logins()) == 1


async def test_a_portal_hop_that_ends_signed_out_leads_to_the_login():
    site = _site()
    session = _session(site)
    await session.async_login()
    site.expire_my()
    site.hop_works = False  # the portal is signed in, its hop is not
    site.calls.clear()
    with pytest.raises(DpdApiError):
        await session.async_get_inbox()
    assert len(site.hops()) == 2  # rung 2, then the post-login hop
    assert len(site.logins()) == 1


@pytest.mark.parametrize("failing", ["inbox", "portal"])
@pytest.mark.parametrize("status", [429, 503])
async def test_http_errors_in_the_cheap_rungs_fail_the_poll_without_a_login(
    failing, status
):
    site = _site()
    session = _session(site)
    await session.async_login()
    site.expire_all()
    site.calls.clear()
    original_my, original_portal = site._my, site._portal
    if failing == "inbox":
        site._my = lambda m, p, d: FakeResponse(status)
    else:
        site._portal = lambda m, p, d: FakeResponse(status)
    with pytest.raises(DpdApiError) as err:
        await session.async_get_inbox()
    assert err.value.status_code == status
    assert site.logins() == []
    site._my, site._portal = original_my, original_portal


async def test_an_error_page_or_foreign_redirect_in_the_cheap_rungs_fails_without_a_login():
    for response in (
        FakeResponse(200, "<html><title>Runtime Error</title></html>"),
        FakeResponse(200, "{}", content_type="application/json"),
        redirect("https://example.invalid/login"),
    ):
        site = _site()
        session = _session(site)
        site._my = lambda m, p, d, response=response: response
        with pytest.raises(DpdApiError):
            await session.async_get_inbox()
        assert site.logins() == []


async def test_a_changed_portal_layout_fails_without_a_login():
    site = _site()
    session = _session(site)
    await session.async_login()
    site.expire_my()
    site.calls.clear()
    broken = de_portal_page().replace('name="__VIEWSTATE"', 'name="x"')
    site._portal = lambda m, p, d: FakeResponse(200, broken)
    with pytest.raises(DpdApiError):
        await session.async_get_inbox()
    assert site.logins() == []


# -- lock ------------------------------------------------------------------


async def test_two_concurrent_polls_produce_one_login():
    site = _site()
    site.yield_on_request = True
    session = _session(site)
    first, second = await asyncio.gather(
        session.async_get_inbox(), session.async_get_inbox()
    )
    assert len(site.logins()) == 1
    assert first["incoming"][0]["barcode"] == second["incoming"][0]["barcode"]


# -- captcha ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["captcha_form", "captcha_sitekey", "captcha_script"])
async def test_each_captcha_marker_alone_raises_the_captcha_error(mode, caplog):
    site = _site(login_mode=mode)
    with caplog.at_level(logging.WARNING), pytest.raises(DpdCaptchaError):
        await _session(site).async_login()
    assert "captcha" in caplog.text


async def test_the_pristine_login_form_is_not_a_captcha():
    """The inline CSS rule names the captcha class; that must not count."""
    site = _site(login_mode="form_no_message")
    with pytest.raises(DpdApiError) as err:
        await _session(site).async_login()
    assert not isinstance(err.value, (DpdCaptchaError, DpdAuthError))


async def test_the_captcha_warning_is_logged_once(caplog):
    site = _site(login_mode="captcha_form")
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            with pytest.raises(DpdCaptchaError):
                await _session(site).async_login()
    assert len([r for r in caplog.records if "captcha" in r.getMessage()]) == 1


async def test_the_captcha_is_never_a_credential_error():
    assert issubclass(DpdCaptchaError, DpdApiError)
    assert not issubclass(DpdCaptchaError, DpdAuthError)
    assert "captcha" in str(DpdCaptchaError())


async def test_backoff_blocks_the_login_rung_but_not_the_cheap_ones(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(de_session.time, "monotonic", lambda: clock[0])
    site = _site()
    session = _session(site)
    await session.async_login()
    site.login_mode = "captcha_form"
    site.expire_all()
    with pytest.raises(DpdCaptchaError):
        await session.async_get_inbox()
    assert len(site.logins()) == 2

    # Within six hours: no login attempt at all, but a live session still reads.
    site.calls.clear()
    clock[0] += 5 * 3600
    with pytest.raises(DpdCaptchaError):
        await session.async_get_inbox()
    assert site.logins() == []
    assert [c["url"] for c in site.calls] == [INBOX, PORTAL, PUBLIC_LOGIN]

    site.portal_session = True
    site.my_session = True
    assert (await session.async_get_inbox())["incoming"]
    assert site.logins() == []

    # Six hours after the captcha the login is tried again.
    site.expire_all()
    site.login_mode = "ok"
    clock[0] += 2 * 3600
    assert (await session.async_get_inbox())["incoming"]
    assert len(site.logins()) == 1


async def test_only_the_config_flow_bypasses_the_backoff_and_a_success_clears_it(
    monkeypatch,
):
    clock = [1000.0]
    monkeypatch.setattr(de_session.time, "monotonic", lambda: clock[0])
    site = _site(login_mode="captcha_form")
    with pytest.raises(DpdCaptchaError):
        await _session(site).async_login()
    assert len(site.logins()) == 1

    # A poll honours the back-off and does not touch the login.
    poller = _session(site)
    with pytest.raises(DpdCaptchaError):
        await poller.async_get_inbox()
    assert len(site.logins()) == 1

    # Setup honours the back-off: no login, no network.
    with pytest.raises(DpdCaptchaError):
        await _session(site).async_login(respect_backoff=True)
    assert len(site.logins()) == 1

    # The config flow (async_login) always makes a real attempt; once it
    # works the back-off is gone.
    site.login_mode = "ok"
    await _session(site).async_login()
    assert len(site.logins()) == 2
    assert not de_session._CAPTCHA_UNTIL
    site.expire_all()
    assert (await poller.async_get_inbox())["incoming"]
    assert len(site.logins()) == 3


# -- error table -----------------------------------------------------------


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        ("no_token", 302),
        ("other_host", 302),
        ("no_location", 302),
        ("other_200", 200),
        ("form_no_message", 200),
        ("403", 403),
        ("429", 429),
        ("500", 500),
        ("503", 503),
    ],
)
async def test_login_problems_are_api_errors_never_auth(mode, status):
    site = _site(login_mode=mode)
    with pytest.raises(DpdApiError) as err:
        await _session(site).async_login()
    assert type(err.value) is DpdApiError
    assert err.value.status_code == status


@pytest.mark.parametrize("mode", ["rejected", "rejected_field"])
async def test_a_rejected_login_with_a_message_is_the_only_auth_error(mode):
    site = _site(login_mode=mode)
    with pytest.raises(DpdAuthError):
        await _session(site).async_login()


async def test_a_form_that_only_echoes_the_username_is_not_an_auth_error():
    """A re-render without a message stays uncertain, so it never reauths."""
    site = _site(login_mode="echoed_username")
    with pytest.raises(DpdApiError) as err:
        await _session(site).async_login()
    assert type(err.value) is DpdApiError


@pytest.mark.parametrize("status", [404, 429, 500, 502, 503])
async def test_http_errors_are_api_errors_carrying_the_status(status):
    site = _site()
    session = _session(site)
    site._respond = lambda m, u, d: FakeResponse(status)
    with pytest.raises(DpdApiError) as err:
        await session._fetch("GET", INBOX)
    assert err.value.status_code == status


async def test_non_html_is_an_api_error():
    site = _site()
    session = _session(site)
    site._respond = lambda m, u, d: FakeResponse(
        200, "{}", content_type="application/json"
    )
    with pytest.raises(DpdApiError):
        await session._fetch("GET", INBOX)


async def test_a_missing_content_type_is_an_api_error():
    site = _site()
    session = _session(site)
    site._respond = lambda m, u, d: FakeResponse(200, "x", content_type=None)
    with pytest.raises(DpdApiError):
        await session._fetch("GET", INBOX)


async def test_a_redirect_without_a_location_is_an_api_error():
    site = _site()
    session = _session(site)
    site._respond = lambda m, u, d: FakeResponse(302)
    with pytest.raises(DpdApiError):
        await session._fetch("GET", INBOX)


@pytest.mark.parametrize(
    "target",
    [
        "https://my.dpd.de/Error.aspx?code=1",
        "https://my.dpd.de/page.aspx?showerror=1",
    ],
)
async def test_an_error_page_redirect_is_an_api_error(target):
    site = _site()
    session = _session(site)
    site._respond = lambda m, u, d: redirect(target)
    with pytest.raises(DpdApiError):
        await session._fetch("GET", INBOX)


async def test_a_runtime_error_page_is_an_api_error():
    site = _site()
    session = _session(site)
    site._respond = lambda m, u, d: FakeResponse(
        200, "<html><title>Server Runtime Error</title></html>"
    )
    with pytest.raises(DpdApiError):
        await session._fetch("GET", INBOX)


async def test_a_redirect_to_a_foreign_host_is_refused():
    site = _site()
    session = _session(site)
    site._respond = lambda m, u, d: redirect("https://example.invalid/x")
    with pytest.raises(DpdApiError) as err:
        await session._fetch("GET", INBOX)
    assert err.value.status_code == 502
    assert len(site.calls) == 1


async def test_a_redirect_loop_ends():
    site = _site()
    session = _session(site)
    site._respond = lambda m, u, d: redirect(INBOX)
    with pytest.raises(DpdApiError) as err:
        await session._fetch("GET", INBOX)
    assert err.value.status_code == 310


async def test_a_postback_source_without_form_state_is_an_api_error_and_one_warning(
    caplog,
):
    site = _site()
    session = _session(site)
    broken = de_portal_page().replace('name="__VIEWSTATE"', 'name="x"')
    with caplog.at_level(logging.WARNING):
        for _ in range(2):
            with pytest.raises(DpdApiError):
                await session._postback(PORTAL, broken, "t")
    assert site.calls == []
    assert len([r for r in caplog.records if "__VIEWSTATE" in r.getMessage()]) == 1


# -- reading ---------------------------------------------------------------


async def test_the_other_tabs_are_skipped_while_their_counters_are_zero():
    site = _site()
    session = _session(site)
    await session.async_login()
    site.calls.clear()
    inbox = await session.async_get_inbox()
    assert inbox["sending"] == [] and inbox["returning"] == []
    assert not [c for c in site.calls if c["target"] and "btnGetSending" in c["target"]]
    assert not [
        c for c in site.calls if c["target"] and "btnGetReturning" in c["target"]
    ]


async def test_a_tab_postback_carries_the_form_state_of_the_page_before_it():
    site = _site()
    site.tabs["sending"] = [_row(DE_BARCODE_2, "Retoure unterwegs", 3)]
    site.details[DE_BARCODE_2] = _detail(DE_BARCODE_2)
    session = _session(site)
    await session.async_login()
    site.calls.clear()
    inbox = await session.async_get_inbox()

    posts = [c for c in site.calls if c["method"] == "POST"]
    states = [c["data"]["__VIEWSTATE"] for c in posts]
    # incoming select, sending tab, sending select: each from the page before
    assert states == ["VS-2-incoming", "VS-3-incoming", "VS-4-sending"]
    assert inbox["sending"][0]["tab"] == "sending"
    assert inbox["sending"][0]["detail"]["identity"] == DE_BARCODE_2


async def test_the_returning_tab_is_read_too():
    site = _site()
    site.tabs["returning"] = [_row(DE_BARCODE_2, "Retoure", 3)]
    site.details[DE_BARCODE_2] = _detail(DE_BARCODE_2)
    session = _session(site)
    await session.async_login()
    inbox = await session.async_get_inbox()
    assert [p["barcode"] for p in inbox["returning"]] == [DE_BARCODE_2]


async def test_an_unreadable_counter_skips_its_tab():
    site = _site(counters_hidden=True)
    session = _session(site)
    await session.async_login()
    inbox = await session.async_get_inbox()
    assert inbox["sending"] == []
    assert inbox["incoming"]


async def test_a_signed_out_tab_page_fails_the_poll():
    site = _site()
    site.tabs["sending"] = [_row(DE_BARCODE_2)]
    site.details[DE_BARCODE_2] = _detail(DE_BARCODE_2)
    session = _session(site)
    await session.async_login()
    original = site._my

    def drop(method, parsed, data):
        if data.get("__EVENTTARGET", "").endswith("btnGetSendingParcel"):
            return FakeResponse(200, de_inbox_page([], signed_in=False))
        return original(method, parsed, data)

    site._my = drop
    with pytest.raises(DpdApiError):
        await session.async_get_inbox()


async def test_a_parcel_row_carries_its_list_fields_and_panel():
    site = _site()
    session = _session(site)
    await session.async_login()
    (parcel,) = (await session.async_get_inbox())["incoming"]
    assert parcel["label"] == "DPD Paket"
    assert parcel["status_sentence"] == "Paket unterwegs"
    assert parcel["status_icon"] == 2
    assert parcel["detail"]["weight_text"] == "10,95"
    assert parcel["stale_detail"] is None


# -- detail cache ----------------------------------------------------------


async def test_an_unchanged_row_is_not_fetched_again_and_a_changed_one_is():
    site = _site()
    session = _session(site)
    await session.async_login()
    await session.async_get_inbox()
    assert len(site.selects()) == 1

    await session.async_get_inbox()
    assert len(site.selects()) == 1  # cache hit: no btnSelectParcel

    site.tabs["incoming"][0]["sentence"] = "Paket in Zustellung"
    (parcel,) = (await session.async_get_inbox())["incoming"]
    assert len(site.selects()) == 2
    assert parcel["stale_detail"] is not None  # the old panel, kept as fallback


async def test_a_parcel_that_left_the_list_is_forgotten():
    site = _site()
    session = _session(site)
    await session.async_login()
    await session.async_get_inbox()
    site.tabs["incoming"] = []
    await session.async_get_inbox()
    site.tabs["incoming"] = [_row()]
    await session.async_get_inbox()
    assert len(site.selects()) == 2


async def test_the_second_row_is_selected_from_the_page_the_first_select_returned():
    site = _site()
    site.tabs["incoming"].append(_row(DE_BARCODE_2))
    site.details[DE_BARCODE_2] = _detail(DE_BARCODE_2)
    session = _session(site)
    await session.async_login()
    inbox = await session.async_get_inbox()
    assert [p["barcode"] for p in inbox["incoming"]] == [DE_BARCODE, DE_BARCODE_2]
    targets = [c["target"] for c in site.selects()]
    assert targets[0].endswith("ctl00$btnSelectParcel")
    assert targets[1].endswith("ctl01$btnSelectParcel")


# -- one bad row does not fail the poll ------------------------------------


@pytest.mark.parametrize("mode", ["mismatch", "error", "signed_out", "no_panel"])
async def test_a_bad_detail_leaves_a_row_only_parcel_and_the_poll_succeeds(mode):
    site = _site()
    site.tabs["incoming"].append(_row(DE_BARCODE_2))
    site.details[DE_BARCODE_2] = _detail(DE_BARCODE_2)
    site.select_mode[DE_BARCODE] = mode
    session = _session(site)
    await session.async_login()
    inbox = await session.async_get_inbox()
    first, second = inbox["incoming"]
    assert first["detail"] is None
    assert first["status_sentence"] == "Paket unterwegs"
    assert second["detail"]["identity"] == DE_BARCODE_2


async def test_a_failed_detail_is_retried_on_the_next_poll():
    site = _site()
    site.select_mode[DE_BARCODE] = "error"
    session = _session(site)
    await session.async_login()
    await session.async_get_inbox()
    del site.select_mode[DE_BARCODE]
    (parcel,) = (await session.async_get_inbox())["incoming"]
    assert parcel["detail"] is not None


async def test_a_barcode_in_two_tabs_keeps_a_detail_slot_per_tab():
    site = _site()
    site.tabs["sending"] = [_row(DE_BARCODE, "Retoure unterwegs", 3)]
    session = _session(site)
    await session.async_login()
    first = await session.async_get_inbox()
    assert first["incoming"][0]["detail"] and first["sending"][0]["detail"]
    selects = len(site.selects())
    again = await session.async_get_inbox()
    assert len(site.selects()) == selects  # both served from their own slot
    assert again["sending"][0]["detail"] is first["sending"][0]["detail"]


async def test_setup_honours_the_unopened_backoff_too():
    site = _site(hop_works=False)
    with pytest.raises(DpdApiError):
        await _session(site).async_login(respect_backoff=True)
    assert len(site.logins()) == 1
    with pytest.raises(DpdApiError):
        await _session(site).async_login(respect_backoff=True)
    assert len(site.logins()) == 1
    site.hop_works = True
    await _session(site).async_login()  # the flow ignores and clears it
    assert len(site.logins()) == 2
    assert not de_session._UNOPENED_UNTIL


async def test_an_anonymous_parcel_list_is_a_clean_signed_out_answer():
    """200 on the parcel host, zero counters, no marker: try the next rung."""
    site = _site()
    session = _session(site)
    html = await session._fetch("GET", INBOX)
    assert "btnLogout" not in html and "Meine Pakete" in html
    assert await session._async_reuse_session() is None
    assert [c["url"] for c in site.calls[-3:]] == [INBOX, PORTAL, PUBLIC_LOGIN]


async def test_an_anonymous_portal_redirecting_to_the_public_login_page_leads_to_the_login():
    """That page says "Abmelden" too; it is signed out, not an error."""
    site = _site()
    session = _session(site)
    url, html = await session._fetch_page("GET", PORTAL)
    assert urlparse(url).hostname == "www.dpd.com"
    assert "Abmelden" in html
    assert (await session.async_get_inbox())["incoming"]
    assert len(site.logins()) == 1
    assert site.hops() and len(site.hops()) == 1  # only after the login


async def test_a_signed_out_portal_page_served_in_place_leads_to_the_login():
    site = _site(portal_in_place=True)
    session = _session(site)
    assert (await session.async_get_inbox())["incoming"]
    assert len(site.logins()) == 1
