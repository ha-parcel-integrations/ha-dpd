"""DPD UK consumer-app account: Firebase magic-link sign-in and parcel lists."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

import aiohttp

from ...const import (
    DPD_UK_ANDROID_PACKAGE,
    DPD_UK_API_HOST,
    DPD_UK_AUTH_PREFIX,
    DPD_UK_DATA_PREFIX,
    DPD_UK_FIREBASE_API_KEY,
    DPD_UK_FIREBASE_SIGN_IN_URL,
    DPD_UK_FIREBASE_TOKEN_URL,
    DPD_UK_LOGIN_HOST,
    DPD_UK_WEB_FIREBASE_API_KEY,
    UK_SOURCE_APP,
    UK_SOURCE_WEB,
    DpdApiError,
    DpdAuthError,
)

_LOGGER = logging.getLogger(__name__)
_ISSUE_URL = "https://github.com/ha-parcel-integrations/ha-dpd/issues/new?template=unrecognised_status.yml"
_LINK_ERRORS = ("EXPIRED_OOB_CODE", "INVALID_OOB_CODE")
_REJECTED_REFRESH = (
    "TOKEN_EXPIRED",
    "INVALID_REFRESH_TOKEN",
    "USER_DISABLED",
    "USER_NOT_FOUND",
)
_DEFAULT_ID_TOKEN_LIFETIME = 3600
_CLAIMS_REVOKE_GRACE = 2.0
_REFUSALS_LOGGED: set[str] = set()


class DpdUkLinkError(DpdAuthError):
    """The pasted magic link is expired, used or malformed."""


class DpdUkNotConsumerError(DpdAuthError):
    """The account has no DPD consumer profile (no ``consumerId`` claim)."""


class DpdUkUnrecognisedLinkError(DpdAuthError):
    """The pasted text is not a DPD UK sign-in link of either kind."""


class DpdUkEmailRequiredError(DpdAuthError):
    """The app's e-mailed link needs the e-mail address it was sent to."""


def classify_link(link: str) -> tuple[str, str]:
    """Route a pasted link to its verify front door.

    Returns ``(UK_SOURCE_WEB, verificationId)`` for a website link, or
    ``(UK_SOURCE_APP, inner action URL)`` for the app's e-mailed Firebase link
    (usually wrapped in a dynamic link carrying the action URL in ``link``).
    """
    link = link.strip()
    parsed = urlparse(link)
    if not parsed.scheme.startswith("http") or not parsed.netloc:
        raise DpdUkUnrecognisedLinkError("Unrecognised sign-in link")
    query = parse_qs(parsed.query)
    verification = (query.get("verificationId") or [""])[0]
    if verification:
        return UK_SOURCE_WEB, verification
    inner = query.get("link")
    candidate = inner[0] if inner else link
    if "oobCode=" not in candidate:
        raise DpdUkUnrecognisedLinkError("Unrecognised sign-in link")
    return UK_SOURCE_APP, candidate


def decode_claims(id_token: str) -> dict[str, Any]:
    """Read the claims of a Firebase id token (unverified, never logged)."""
    try:
        payload = id_token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError):
        return {}
    return claims if isinstance(claims, dict) else {}


def _text_has(body: Any, needles: tuple[str, ...]) -> bool:
    text = json.dumps(body) if not isinstance(body, str) else body
    return any(needle in text for needle in needles)


class DpdUkSession:
    """Own one entry's Firebase token and device session for the UK app backend.

    Only the Firebase refresh token is persisted. The id token and the
    ``dpdSession`` live in memory and are re-minted on demand.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str | None,
        device_id: str,
        token_updater: Callable[[str], None] | None = None,
        source: str = UK_SOURCE_APP,
    ) -> None:
        """Initialize with stored refresh material, if any."""
        self._session = session
        self.refresh_token = refresh_token
        self.device_id = device_id
        self.source = source
        self._token_updater = token_updater
        self.consumer_id: str | None = None
        self._id_token: str | None = None
        self._expires_at: datetime | None = None
        self._dpd_session: str | None = None
        self._lock = asyncio.Lock()

    # -- low-level transport -------------------------------------------------

    async def _json(self, method: str, url: str, **kwargs: Any) -> tuple[int, Any]:
        async with self._session.request(method, url, **kwargs) as response:
            try:
                body = await response.json(content_type=None)
            except ValueError:
                body = None
            return response.status, body

    @staticmethod
    def _data(body: Any, key: str) -> str | None:
        data = body.get("data") if isinstance(body, dict) else None
        value = data.get(key) if isinstance(data, dict) else None
        return value if isinstance(value, str) and value else None

    def _set_refresh_token(self, token: str | None) -> None:
        if token and token != self.refresh_token:
            self.refresh_token = token
            if self._token_updater:
                self._token_updater(token)

    # -- Firebase ------------------------------------------------------------

    def _firebase_key(self) -> str:
        return (
            DPD_UK_WEB_FIREBASE_API_KEY
            if self.source == UK_SOURCE_WEB
            else DPD_UK_FIREBASE_API_KEY
        )

    def _android_headers(self) -> dict[str, str]:
        # Only the app's key is tied to the app; the website key is not sent
        # as the Android client.
        if self.source == UK_SOURCE_WEB:
            return {}
        return {"X-Android-Package": DPD_UK_ANDROID_PACKAGE}

    def _store_firebase(self, body: Any) -> None:
        id_token = body.get("idToken") or body.get("id_token")
        if not isinstance(id_token, str) or not id_token:
            raise DpdAuthError("DPD UK sign-in returned no id token")
        self._id_token = id_token
        self._set_refresh_token(body.get("refreshToken") or body.get("refresh_token"))
        try:
            lifetime = float(body.get("expiresIn") or body.get("expires_in"))
        except (TypeError, ValueError):
            lifetime = _DEFAULT_ID_TOKEN_LIFETIME
        self._expires_at = datetime.now(UTC) + timedelta(seconds=lifetime)
        claim = decode_claims(id_token).get("consumerId")
        self.consumer_id = claim if isinstance(claim, str) and claim else None

    async def _sign_in_with_custom_token(self, custom_token: str) -> None:
        status, body = await self._json(
            "POST",
            DPD_UK_FIREBASE_SIGN_IN_URL,
            params={"key": self._firebase_key()},
            headers=self._android_headers(),
            json={"token": custom_token, "returnSecureToken": True},
        )
        if status in (400, 401, 403):
            raise DpdAuthError(f"DPD UK rejected the sign-in token (HTTP {status})")
        if status >= 300 or not isinstance(body, dict):
            raise DpdApiError(status)
        self._store_firebase(body)

    async def _refresh_id_token(self) -> None:
        if not self.refresh_token:
            raise DpdAuthError("DPD UK has no refresh token")
        status, body = await self._json(
            "POST",
            DPD_UK_FIREBASE_TOKEN_URL,
            params={"key": self._firebase_key()},
            headers=self._android_headers(),
            data={"grant_type": "refresh_token", "refresh_token": self.refresh_token},
        )
        if status == 400 and _text_has(body, _REJECTED_REFRESH):
            raise DpdAuthError("DPD UK rejected the stored refresh token")
        if status >= 300 or not isinstance(body, dict):
            # Not a confirmed rejection: surface it, never push into reauth.
            raise DpdApiError(status)
        self._store_firebase(body)

    async def _upgrade_claims(self) -> None:
        """Run ``refreshUserClaims`` and re-sign-in so the id token has the claims."""
        status, body = await self._json(
            "POST",
            f"{DPD_UK_API_HOST}{DPD_UK_AUTH_PREFIX}/auth/refreshUserClaims",
            headers={"Authorization": f"Bearer {self._id_token}"},
        )
        if status in (401, 403):
            raise DpdAuthError(f"DPD UK refused the claims upgrade (HTTP {status})")
        custom_token = self._data(body, "customToken")
        if status >= 300 or not custom_token:
            raise DpdApiError(status)
        await self._sign_in_with_custom_token(custom_token)

    async def _ensure_id_token(self) -> None:
        if (
            self._id_token is None
            or self._expires_at is None
            or datetime.now(UTC) >= self._expires_at - timedelta(seconds=60)
        ):
            await self._refresh_id_token()
        if self.consumer_id is None and self.source == UK_SOURCE_APP:
            await self._upgrade_claims()
        if self.consumer_id is None:
            raise DpdUkNotConsumerError("DPD UK account has no consumer profile")

    # -- DPD device session --------------------------------------------------

    async def _mint_dpd_session(self) -> None:
        claims = decode_claims(self._id_token or "")
        status, body = await self._json(
            "POST",
            f"{DPD_UK_API_HOST}{DPD_UK_DATA_PREFIX}/device/login",
            headers={"Authorization": f"Bearer {self._id_token}"},
            json={
                "consumerId": self.consumer_id,
                "deviceId": self.device_id,
                "sms": claims.get("phone_number") or "",
            },
        )
        if status in (401, 403):
            raise DpdAuthError(f"DPD UK refused the device login (HTTP {status})")
        token = self._data(body, "dpdSession")
        if status >= 300 or not token:
            raise DpdApiError(status)
        self._dpd_session = token

    # -- public --------------------------------------------------------------

    async def async_sign_in_with_link(self, email: str, link: str) -> str:
        """Exchange a pasted sign-in link for a session; return the consumer id.

        A website link (``verificationId``) needs no e-mail address; the app's
        e-mailed link does.
        """
        source, value = classify_link(link)
        if source == UK_SOURCE_APP and not email.strip():
            raise DpdUkEmailRequiredError("The app's link needs the e-mail address")
        self.source = source
        async with self._lock:
            if source == UK_SOURCE_WEB:
                custom_token = await self._verify_web(value)
            else:
                custom_token = await self._verify_app(email, value)
            await self._sign_in_with_custom_token(custom_token)
            if source == UK_SOURCE_APP:
                await self._upgrade_claims()
            # The website's token already carries the consumer claim.
            if self.consumer_id is None:
                raise DpdUkNotConsumerError("DPD UK account has no consumer profile")
            await self._prove_refresh_token()
            await self._mint_dpd_session()
            return self.consumer_id

    async def _prove_refresh_token(self) -> None:
        """Spend the refresh token once before the entry is allowed to keep it.

        A token has been seen dead on arrival: sign-in succeeded, and the first
        poll hours later was refused. Stored unspent, that passes setup and
        surfaces as a reauth nobody can explain. The cause is not understood,
        so the app door also retries the claims upgrade once.
        """
        try:
            await self._refresh_id_token()
        except DpdAuthError:
            if self.source != UK_SOURCE_APP:
                raise
            await asyncio.sleep(_CLAIMS_REVOKE_GRACE)
            await self._upgrade_claims()
            await self._refresh_id_token()

    async def _verify_web(self, verification_id: str) -> str:
        status, body = await self._json(
            "GET",
            f"{DPD_UK_LOGIN_HOST}/auth/verifyMagicLink",
            params={"verificationId": verification_id},
        )
        # Nothing but the link itself is being checked here, so a refusal
        # means the link is spent or wrong.
        if status in (400, 401, 403, 404, 410) or _text_has(body, _LINK_ERRORS):
            raise DpdUkLinkError("The sign-in link is expired or already used")
        custom_token = self._data(body, "customToken")
        if status >= 300 or not custom_token:
            raise DpdApiError(status)
        return custom_token

    async def _verify_app(self, email: str, action_url: str) -> str:
        status, body = await self._json(
            "GET",
            f"{DPD_UK_API_HOST}{DPD_UK_AUTH_PREFIX}/auth/verifyMagicLink",
            params={"email": email.strip(), "link": action_url},
        )
        if _text_has(body, _LINK_ERRORS):
            raise DpdUkLinkError("The magic link is expired or already used")
        if status in (400, 401, 403, 404):
            raise DpdAuthError(f"DPD UK rejected the magic link (HTTP {status})")
        custom_token = self._data(body, "customToken")
        if status >= 300 or not custom_token:
            raise DpdApiError(status)
        return custom_token

    async def async_login(self) -> None:
        """Re-establish a session from the stored refresh token."""
        async with self._lock:
            await self._ensure_id_token()
            await self._mint_dpd_session()

    async def _async_get(self, bucket: str) -> list[dict[str, Any]]:
        async with self._lock:
            await self._ensure_id_token()
            if self._dpd_session is None:
                await self._mint_dpd_session()
            for attempt in range(2):
                url = (
                    f"{DPD_UK_API_HOST}{DPD_UK_DATA_PREFIX}/consumers/"
                    f"{self.consumer_id}/parcels/{bucket}"
                )
                status, body = await self._json(
                    "GET",
                    url,
                    params={"userType": "R"},
                    headers={
                        "Authorization": f"Bearer {self._id_token}",
                        "dpdSession": self._dpd_session or "",
                    },
                )
                if status == 401 and attempt == 0:
                    self._id_token = None
                    await self._ensure_id_token()
                    continue
                if (
                    status == 403
                    and attempt == 0
                    and _text_has(body, ("session is invalid",))
                ):
                    await self._mint_dpd_session()
                    continue
                if status == 401:
                    raise DpdAuthError("DPD UK rejected the refreshed credentials")
                if status == 403:
                    self._warn_refused(bucket)
                    raise DpdApiError(status)
                if status >= 300:
                    raise DpdApiError(status)
                data = body.get("data") if isinstance(body, dict) else None
                if not isinstance(data, list):
                    self._warn_refused(f"{bucket}-shape")
                    raise DpdApiError(status)
                return [item for item in data if isinstance(item, dict)]
        raise DpdApiError(403)  # pragma: no cover - the loop always returns/raises

    @staticmethod
    def _warn_refused(label: str) -> None:
        if label in _REFUSALS_LOGGED:
            return
        _REFUSALS_LOGGED.add(label)
        _LOGGER.warning(
            "DPD UK refused or garbled a parcel-list request (%s) — report it at %s",
            label,
            _ISSUE_URL,
        )

    async def async_get_parcels(
        self,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Return the received parcels as ``(active, inactive)``."""
        active = await self._async_get("active")
        inactive = await self._async_get("inactive")
        return active, inactive
