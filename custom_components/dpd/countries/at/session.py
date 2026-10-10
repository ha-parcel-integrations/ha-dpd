"""DPD Austria mydpd.at portal session and parcel-inbox client.

One `POST jws.php/<method>` shape for everything, with a JSON array of
*positional* arguments as the body and a Bearer JWT obtained from the user's
own login. There is no refresh route anywhere in the portal, so an expired
token is recovered by logging in again — the credentials are what the config
entry persists, never the JWT.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

from ...const import (
    DPD_AT_API_URL,
    DPD_AT_BASE_URL,
    DPD_AT_DIR_INCOMING,
    DPD_AT_DIR_RETURNS,
    DPD_AT_DIR_SENT,
    DPD_AT_USER_AGENT,
    DpdApiError,
    DpdAuthError,
)

_LOGGER = logging.getLogger(__name__)
_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-dpd/issues/new?template=unrecognised_status.yml"
# A refusal persists across polls, so its WARNING is emitted once per distinct
# (method, envelope shape) instead of every 15-45 minutes. The coordinator
# logs the resulting failure separately anyway.
_REFUSALS_LOGGED: set[tuple[str, tuple[str, ...]]] = set()

_DIRECTIONS = (DPD_AT_DIR_INCOMING, DPD_AT_DIR_SENT, DPD_AT_DIR_RETURNS)


class DpdAtSession:
    """Own one entry's JWT lifecycle and read-only mydpd.at parcel inbox."""

    def __init__(
        self, session: aiohttp.ClientSession, email: str, password: str
    ) -> None:
        """Initialize the session with the user's own portal credentials."""
        self._session = session
        self._email = email
        self._password = password
        self._jwt: str | None = None
        self._login_lock = asyncio.Lock()
        self._primed = False

    async def async_login(self) -> None:
        """Exchange the user's credentials for a portal JWT."""
        async with self._login_lock:
            await self._async_login_locked()

    async def _async_prime(self) -> None:
        """Fetch the portal page once, so the session holds what it issues.

        ``mydpd.at`` is a PHP application behind bot protection: a browser
        reaches ``jws.php`` only after loading a page, which is what sets the
        PHP session and the protection's own cookies. Going straight to the
        API gives none of that, and the portal can then refuse an otherwise
        valid call. Best-effort — if this fails the login is still attempted,
        so a changed landing page cannot break setup on its own.
        """
        if self._primed:
            return
        self._primed = True
        try:
            async with self._session.get(
                DPD_AT_BASE_URL,
                headers={"User-Agent": DPD_AT_USER_AGENT},
            ) as response:
                await response.read()
                _LOGGER.debug(
                    "DPD Austria portal primed (HTTP %s)", response.status
                )
        except aiohttp.ClientError as err:
            _LOGGER.debug("DPD Austria portal could not be primed: %s", err)

    async def _async_login_locked(self) -> None:
        await self._async_prime()
        self._jwt = None
        await self._async_call(
            "usr/login", [self._email, self._password], authorize=False
        )
        # The token is lifted by ``_unwrap``, which is the single place that
        # handles it — every response may carry a rotated one, not just this.
        if self._jwt is None:
            # The credentials were accepted — the response just had no token
            # where one belongs. That is a shape problem, so it must not send
            # the user into reauth.
            raise DpdApiError(200)

    async def _async_call(
        self, method: str, args: list[Any], *, authorize: bool = True
    ) -> dict[str, Any]:
        """Perform one ``jws.php`` call and unwrap its envelope.

        The envelope's own ``state`` is ``"success"`` or ``"failure"`` and is
        **not** the parcel's ``lifecycle.state``. A rejected call answers
        HTTP 200 with ``state: "failure"``, so the body decides the outcome,
        not the status code.

        The portal distinguishes the two failure kinds, and so does this:
        **HTTP 401/403** is a token problem (the portal's own client has a
        dedicated 401 handler for exactly that), while an HTTP 200 carrying
        ``state: "failure"`` is an application-level refusal. Only the login
        call may turn the latter into an auth error — on a data call it is a
        shape or state problem, and pushing the user into reauth for it would
        be wrong, the same rule Germany's SOAP faults follow.
        """
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": DPD_AT_USER_AGENT,
            "Origin": DPD_AT_BASE_URL,
            "Referer": f"{DPD_AT_BASE_URL}/",
            "X-Requested-With": "XMLHttpRequest",
        }
        if authorize:
            if self._jwt is None:
                raise DpdAuthError("DPD Austria has no session token")
            headers["Authorization"] = f"Bearer {self._jwt}"

        async with self._session.post(
            f"{DPD_AT_API_URL}/{method}", json=args, headers=headers
        ) as response:
            if response.status == 401:
                raise DpdAuthError("DPD Austria rejected the session token")
            # 403 is deliberately NOT an auth error. It is the signature of
            # the host's bot protection, and treating it as one would send a
            # user with perfectly good credentials into reauth. It takes the
            # same transient path as 429.
            if response.status >= 300:
                raise DpdApiError(response.status)
            body = await response.json(content_type=None)

        if not isinstance(body, dict):
            raise DpdApiError(response.status)

        body = self._unwrap(body)
        if body.get("state") != "success":
            if not authorize:
                # The login call: a refusal here really is bad credentials.
                raise DpdAuthError("DPD Austria rejected the login")
            # A data call refused after a successful login. Report the
            # envelope's *keys* so the cause is diagnosable from a user's log
            # without ever printing parcel or account values.
            shape = (method, tuple(sorted(body)))
            if shape not in _REFUSALS_LOGGED:
                _REFUSALS_LOGGED.add(shape)
                _LOGGER.warning(
                    "DPD Austria refused %s after a successful login "
                    "(envelope keys=%s, state=%r). Report it at %s",
                    method,
                    sorted(body),
                    body.get("state"),
                    _ISSUE_URL,
                )
            raise DpdApiError(response.status)
        return body

    def _unwrap(self, body: dict[str, Any]) -> dict[str, Any]:
        """Apply the portal's own envelope handling, including token rotation.

        A data call answers ``{"token": ..., "data": {...}}`` — note it has
        **no top-level ``state``**. The portal's client lifts the token out,
        and if ``data`` is then the only key left it descends into it, so the
        real ``state`` lives one level down:

        ``{token, data: {state: "success", data: {inc, send, ret}}}``

        The login call answers ``{state, token, data}`` instead, which still
        has two keys after the token is lifted, so it is not descended into.
        That difference is why login worked while every data call did not.

        **The token rotates on every response that carries one** and the
        portal's client reassigns it each time, so this does too — keeping a
        stale one would expire the session.

        The descent is not conditional on a token being present, although the
        portal's own client makes it so. A response that omitted one would
        otherwise keep its nested envelope and be read as a refusal, and
        descending when ``data`` is the sole key cannot misread the confirmed
        shape.
        """
        token = body.get("token")
        if isinstance(token, str) and token:
            self._jwt = token
        body = {key: value for key, value in body.items() if key != "token"}
        inner = body.get("data")
        if len(body) == 1 and isinstance(inner, dict):
            return inner
        return body

    async def _async_authorized(
        self, method: str, args: list[Any]
    ) -> dict[str, Any]:
        """Call ``method``, re-logging in once if the token has expired.

        There is no refresh route, so the only recovery is a fresh login. It
        is attempted exactly once — a second failure is a real credential
        problem and belongs in reauth. A missing token is logged in for up
        front rather than through the retry path, so that path means only
        "the token was rejected".
        """
        if self._jwt is None:
            await self.async_login()
        try:
            return await self._async_call(method, args)
        except DpdAuthError:
            _LOGGER.debug(
                "DPD Austria session rejected on %s; logging in again once",
                method,
            )
        async with self._login_lock:
            await self._async_login_locked()
        return await self._async_call(method, args)

    async def async_get_parcels(self) -> dict[str, list[dict[str, Any]]]:
        """Return the account inbox, keyed by direction.

        One call carries all three directions. Each is
        ``{"data": [parcel, ...], "max": <total>}``; only ``data`` is read,
        and ``max`` is left to the portal's own pagination, which this
        read-only integration does not drive.
        """
        body = await self._async_authorized("parcel/loadList", [None, None, None])
        data = body.get("data")
        if not isinstance(data, dict):
            raise DpdApiError(200)
        inbox: dict[str, list[dict[str, Any]]] = {}
        for direction in _DIRECTIONS:
            bucket = data.get(direction)
            parcels = bucket.get("data") if isinstance(bucket, dict) else None
            inbox[direction] = (
                [item for item in parcels if isinstance(item, dict)]
                if isinstance(parcels, list)
                else []
            )
        return inbox
