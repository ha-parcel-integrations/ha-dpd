"""DPD Germany myDPD web-account session and parcel-list client.

The account spans three hosts and is cookie-based, so the session owns a
dedicated cookie jar. Credentials are the user's own; nothing but the jar,
in memory, outlives a login.

Each poll walks a ladder and stops at the first rung that yields a signed-in
parcel list, so a login happens only when the existing session is gone:

1. open the parcel list with the current cookies;
2. repeat the portal-to-parcel-list hop alone;
3. sign in (at most once per poll).

The hop from the portal dashboard is mandatory: without it the parcel list
renders an empty inbox instead of asking for a login.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

import aiohttp

from ...const import (
    DPD_DE_INBOX_URL,
    DPD_DE_LOGIN_URL,
    DPD_DE_PORTAL_HOME_URL,
    DPD_DE_USER_AGENT,
    DpdApiError,
    DpdAuthError,
    DpdCaptchaError,
)
from .page import (
    DpdPageError,
    Page,
    detail,
    form_fields,
    has_captcha,
    is_logged_in,
    is_portal_logged_in,
    list_rows,
    login_rejected,
    warn_pager,
)

_LOGGER = logging.getLogger(__name__)

_MAX_REDIRECTS = 8
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CAPTCHA_BACKOFF_SECONDS = 6 * 60 * 60
_UNOPENED_BACKOFF_SECONDS = 60 * 60
_HOP_TARGET = "ctl00$cphBody$ctl00$btnHomeMyParcel"
_ROW_TARGET = "ctl00$ContentPlaceHolder1$repParcelList$ctl{index:02d}$btnSelectParcel"
_TAB_TARGETS = {
    "incoming": "ctl00$ContentPlaceHolder1$btnGetIncomingParcel",
    "sending": "ctl00$ContentPlaceHolder1$btnGetSendingParcel",
    "returning": "ctl00$ContentPlaceHolder1$btnGetReturningParcel",
}
_ERROR_PAGE = re.compile(r"/error\.aspx|showerror", re.IGNORECASE)
_ERROR_TITLE = re.compile(r"<title>[^<]*runtime error", re.IGNORECASE)

# Both back-offs are properties of the account's recent login history, so they
# outlive the session object (a reload builds a new one). A login that opened
# nothing is held off for less time than a captcha: it may only mean a changed
# page, and must not turn into a login on every poll.
_CAPTCHA_UNTIL: dict[str, float] = {}
_UNOPENED_UNTIL: dict[str, float] = {}
_WARNED: set[str] = set()


def _warn_once(key: str, message: str, *args: Any) -> None:
    if key in _WARNED:
        return
    _WARNED.add(key)
    _LOGGER.warning(message, *args)


def _allowed_host(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "dpd.com" or host.endswith((".dpd.com", ".dpd.de"))


class DpdDeSession:
    """Own one entry's web-account session and read its parcel list."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        """Initialize the session; ``session`` must have its own cookie jar."""
        self._session = session
        self._email = email
        self._password = password
        self._lock = asyncio.Lock()
        # barcode -> (fingerprint of the list row, parsed detail panel)
        self._details: dict[tuple[str, str], tuple[tuple[str, int | None], dict]] = {}

    # -- transport ---------------------------------------------------------

    async def _fetch(self, *args: Any, **kwargs: Any) -> str:
        """Request a page and return its HTML."""
        return (await self._fetch_page(*args, **kwargs))[1]

    async def _fetch_page(
        self,
        method: str,
        url: str,
        *,
        data: dict[str, str] | None = None,
        referer: str | None = None,
    ) -> tuple[str, str]:
        """Request a page, following redirects by hand: ``(final_url, html)``."""
        headers = {
            "User-Agent": DPD_DE_USER_AGENT,
            "Accept-Language": "de-DE,de;q=0.9",
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
        }
        if referer:
            parsed = urlparse(referer)
            headers["Referer"] = referer
            headers["Origin"] = f"{parsed.scheme}://{parsed.netloc}"
        for _ in range(_MAX_REDIRECTS):
            if not _allowed_host(url):
                raise DpdApiError(502)
            if _ERROR_PAGE.search(url):
                raise DpdApiError(500)
            async with self._session.request(
                method, url, data=data, headers=headers, allow_redirects=False
            ) as response:
                status = response.status
                if status in _REDIRECT_STATUSES:
                    location = response.headers.get("Location")
                    if not location:
                        raise DpdApiError(status)
                    url = urljoin(url, location)
                    method, data = "GET", None
                    headers = {
                        key: value
                        for key, value in headers.items()
                        if key not in ("Referer", "Origin")
                    }
                    continue
                if status != 200:
                    raise DpdApiError(status)
                if "html" not in (response.content_type or ""):
                    raise DpdApiError(status)
                body = await response.text(errors="replace")
            if _ERROR_TITLE.search(body):
                raise DpdApiError(500)
            return url, body
        raise DpdApiError(310)

    async def _postback(
        self, url: str, html: str, target: str, *, referer: str | None = None
    ) -> str:
        """Post one WebForms event with the form state of ``html``."""
        try:
            fields = form_fields(html)
        except DpdPageError as err:
            _warn_once(
                f"state:{err.missing}",
                "A DPD Germany page did not carry its form state (%s) — "
                "report it at "
                "https://github.com/ha-parcel-integrations/ha-dpd/issues/new"
                "?template=unrecognised_status.yml",
                err.missing,
            )
            raise
        fields["__EVENTTARGET"] = target
        fields["__EVENTARGUMENT"] = ""
        return await self._fetch("POST", url, data=fields, referer=referer or url)

    # -- login ladder ------------------------------------------------------

    def _check_backoff(self) -> None:
        key, now = self._email.lower(), time.monotonic()
        if _CAPTCHA_UNTIL.get(key, 0.0) > now:
            raise DpdCaptchaError
        if _UNOPENED_UNTIL.get(key, 0.0) > now:
            raise DpdApiError(200)

    async def _async_full_login(self, *, respect_backoff: bool) -> str:
        """Sign in and return the portal dashboard page."""
        if respect_backoff:
            self._check_backoff()
        async with self._session.request(
            "POST",
            DPD_DE_LOGIN_URL,
            data={"dpg_username": self._email, "dpg_password": self._password},
            headers={
                "User-Agent": DPD_DE_USER_AGENT,
                "Accept-Language": "de-DE,de;q=0.9",
                "Origin": "https://www.dpd.com",
                "Referer": DPD_DE_LOGIN_URL,
            },
            allow_redirects=False,
        ) as response:
            status = response.status
            location = response.headers.get("Location", "")
            body = await response.text(errors="replace") if status == 200 else ""

        if status in _REDIRECT_STATUSES:
            target = urljoin(DPD_DE_LOGIN_URL, location)
            if urlparse(target).hostname == "portal.dpd.de" and "dpd_token=" in target:
                return await self._fetch("GET", target)
            raise DpdApiError(status)
        if status != 200:
            raise DpdApiError(status)
        if has_captcha(body):
            _CAPTCHA_UNTIL[self._email.lower()] = (
                time.monotonic() + _CAPTCHA_BACKOFF_SECONDS
            )
            _warn_once(
                "captcha",
                "DPD Germany is asking for a captcha at login, which Home "
                "Assistant cannot answer. The integration keeps polling but "
                "waits six hours between sign-in attempts; re-authenticating "
                "the integration tries at once, and signing in once at "
                "dpd.com in a browser may clear the captcha.",
            )
            raise DpdCaptchaError
        if login_rejected(body):
            raise DpdAuthError("DPD Germany rejected the login")
        raise DpdApiError(200)

    async def _async_hop(self, portal_html: str) -> str:
        """Open the parcel list from the portal dashboard."""
        return await self._postback(
            DPD_DE_PORTAL_HOME_URL, portal_html, _HOP_TARGET
        )

    async def _async_login_and_open(self, *, respect_backoff: bool) -> str:
        portal = await self._async_full_login(respect_backoff=respect_backoff)
        html = await self._async_hop(portal)
        key = self._email.lower()
        if not is_logged_in(html):
            _UNOPENED_UNTIL[key] = time.monotonic() + _UNOPENED_BACKOFF_SECONDS
            _warn_once(
                "hop",
                "Signed in to DPD Germany, but the parcel list did not open. "
                "Report it at "
                "https://github.com/ha-parcel-integrations/ha-dpd/issues/new"
                "?template=unrecognised_status.yml",
            )
            raise DpdApiError(200)
        _CAPTCHA_UNTIL.pop(key, None)
        _UNOPENED_UNTIL.pop(key, None)
        return html

    async def _async_reuse_session(self) -> str | None:
        """Rungs 1 and 2: the parcel list, if the cookies still open it.

        Only a clean signed-out answer returns ``None`` and so leads to a
        login. Any error (5xx, an error page, a changed layout) fails the
        poll instead: a login cannot fix those, and repeating one every poll
        is what could switch the captcha on.
        """
        html = await self._fetch("GET", DPD_DE_INBOX_URL)
        if is_logged_in(html):
            return html
        portal_url, portal = await self._fetch_page("GET", DPD_DE_PORTAL_HOME_URL)
        if not is_portal_logged_in(portal_url, portal):
            return None
        html = await self._async_hop(portal)
        return html if is_logged_in(html) else None

    async def _async_open_inbox(self) -> str:
        html = await self._async_reuse_session()
        if html is not None:
            return html
        return await self._async_login_and_open(respect_backoff=True)

    async def async_login(self, *, respect_backoff: bool = False) -> None:
        """Sign in once, so the first poll starts on a live session.

        A user entering credentials in the config flow always gets a real
        attempt. Setup and polls pass ``respect_backoff``, so Home Assistant's
        automatic retries do not keep submitting logins.
        """
        async with self._lock:
            await self._async_login_and_open(respect_backoff=respect_backoff)

    # -- reading -----------------------------------------------------------

    async def async_get_inbox(self) -> dict[str, list[dict[str, Any]]]:
        """Return the account's parcels, keyed by tab.

        Postbacks are chained on the previous response's form state and are
        never run concurrently.
        """
        async with self._lock:
            html = await self._async_open_inbox()
            first = Page(html)
            counters = {tab: first.counter(tab) for tab in _TAB_TARGETS}
            inbox: dict[str, list[dict[str, Any]]] = {}
            seen: set[tuple[str, str]] = set()
            for tab, target in _TAB_TARGETS.items():
                if tab != "incoming":
                    if not counters[tab]:
                        inbox[tab] = []
                        continue
                    html = await self._postback(DPD_DE_INBOX_URL, html, target)
                    self._require_signed_in(html)
                page = Page(html)
                warn_pager(page)
                parcels = []
                for row in list_rows(page):
                    html, parcel = await self._async_read_row(html, tab, row)
                    seen.add((tab, row["barcode"]))
                    parcels.append(parcel)
                inbox[tab] = parcels
            self._details = {
                key: entry
                for key, entry in self._details.items()
                if key in seen
            }
            return inbox

    @staticmethod
    def _require_signed_in(html: str) -> None:
        if not is_logged_in(html):
            raise DpdApiError(200)

    async def _async_read_row(
        self, html: str, tab: str, row: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Attach a row's detail panel; return the page to chain from next."""
        barcode = row["barcode"]
        fingerprint = (row["status_sentence"], row["status_icon"])
        cached = self._details.get((tab, barcode))
        parcel = {
            "barcode": barcode,
            "label": row["label"],
            "status_sentence": row["status_sentence"],
            "status_icon": row["status_icon"],
            "tab": tab,
            "detail": None,
            "stale_detail": cached[1] if cached else None,
        }
        if cached and cached[0] == fingerprint:
            parcel["detail"] = cached[1]
            return html, parcel
        try:
            fresh = await self._postback(
                DPD_DE_INBOX_URL,
                html,
                _ROW_TARGET.format(index=row["index"]),
            )
            self._require_signed_in(fresh)
        except DpdApiError as err:
            _LOGGER.debug("DPD Germany parcel detail unavailable (%s)", err)
            return html, parcel
        panel = detail(Page(fresh))
        if panel is None or panel["identity"] != barcode:
            _LOGGER.debug("DPD Germany parcel detail did not match its row")
            return fresh, parcel
        self._details[(tab, barcode)] = (fingerprint, panel)
        parcel["detail"] = panel
        return fresh, parcel
