"""DPD Polska public OAuth session and receiver-inbox client."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import aiohttp

from ...const import (
    DPD_PL_API_URL,
    DPD_PL_CLIENT_ID,
    DPD_PL_REDIRECT_URI,
    DPD_PL_SSO_URL,
    DpdApiError,
    DpdAuthError,
)

_TOKEN_URL = f"{DPD_PL_SSO_URL}/auth/realms/DPD/protocol/openid-connect/token"
_MOBILE_HEADERS = {"X-Mobile-Platform": "android", "X-Mobile-Version": "2.10.2"}
_LOGGER = logging.getLogger(__name__)


class DpdPlSession:
    """Own one entry's token lifecycle and read-only DPD Polska inbox."""

    def __init__(self, session: aiohttp.ClientSession, refresh_token: str | None = None, token_updater: Callable[[str], None] | None = None) -> None:
        """Initialize the session with stored refresh material, if any."""
        self._session = session
        self.refresh_token = refresh_token
        self._token_updater = token_updater
        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._refresh_lock = asyncio.Lock()

    async def async_send_sms(self, phone: str) -> None:
        """Ask DPD Polska to send exactly one code to ``phone``."""
        async with self._session.put(f"{DPD_PL_SSO_URL}/api/phone-verifications/{phone}") as response:
            if response.status in (400, 401, 403, 422):
                raise DpdAuthError(f"DPD Poland rejected the phone number ({response.status})")
            if response.status >= 300:
                raise DpdApiError(response.status)

    async def async_register(self, phone: str, code: str) -> dict[str, Any]:
        """Exchange a received SMS code for the public client's token set."""
        async with self._session.post(
            f"{DPD_PL_SSO_URL}/api/users",
            params={"redirect_uri": DPD_PL_REDIRECT_URI, "client_id": DPD_PL_CLIENT_ID},
            json={"emailRegistration": None, "phoneRegistration": {"phone": phone, "code": code}, "type": "PhoneBasedUserRegistrationModel"},
        ) as response:
            if response.status in (400, 401, 403, 422):
                raise DpdAuthError("DPD Poland rejected the SMS code")
            if response.status >= 300:
                raise DpdApiError(response.status)
            body = await response.json(content_type=None)
        auth_code = body.get("code") if isinstance(body, dict) else None
        if not auth_code:
            raise DpdAuthError("DPD Poland did not return an authorization code")
        return await self._async_token({"grant_type": "authorization_code", "code": auth_code, "client_id": DPD_PL_CLIENT_ID})

    async def async_login(self) -> None:
        """Refresh persisted credentials before the first inbox poll."""
        await self._async_refresh()

    async def _async_token(self, data: dict[str, str]) -> dict[str, Any]:
        async with self._session.post(_TOKEN_URL, data=data) as response:
            if response.status in (400, 401, 403):
                _LOGGER.warning(
                    "DPD Poland rejected an OAuth %s grant with HTTP %s; "
                    "reauthentication is required",
                    data.get("grant_type", "unknown"),
                    response.status,
                )
                raise DpdAuthError(
                    f"DPD Poland rejected OAuth {data.get('grant_type')} grant "
                    f"(HTTP {response.status})"
                )
            if response.status >= 300:
                raise DpdApiError(response.status)
            body = await response.json(content_type=None)
        if not isinstance(body, dict) or not body.get("access_token"):
            raise DpdAuthError("DPD Poland token response had no access token")
        self._access_token = body["access_token"]
        new_refresh = body.get("refresh_token")
        if new_refresh:
            self.refresh_token = new_refresh
            if self._token_updater:
                self._token_updater(new_refresh)
        try:
            lifetime = float(body.get("expires_in", 300))
        except (TypeError, ValueError):
            lifetime = 300
        self._expires_at = datetime.now(timezone.utc) + timedelta(seconds=lifetime)
        return body

    async def _async_refresh(self) -> None:
        if not self.refresh_token:
            raise DpdAuthError("DPD Poland has no refresh token")
        async with self._refresh_lock:
            await self._async_token({"grant_type": "refresh_token", "refresh_token": self.refresh_token, "client_id": DPD_PL_CLIENT_ID})

    async def _async_authorized(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(2):
            if self._access_token is None or self._expires_at is None or datetime.now(timezone.utc) >= self._expires_at - timedelta(seconds=60):
                await self._async_refresh()
            headers = {**_MOBILE_HEADERS, **kwargs.pop("headers", {}), "Authorization": f"Bearer {self._access_token}"}
            async with self._session.request(method, url, headers=headers, **kwargs) as response:
                if response.status in (401, 403) and attempt == 0:
                    self._access_token = None
                    continue
                if response.status in (401, 403):
                    raise DpdAuthError("DPD Poland rejected refreshed credentials")
                if response.status >= 300:
                    raise DpdApiError(response.status)
                body = await response.json(content_type=None)
                if not isinstance(body, dict):
                    raise DpdApiError(response.status)
                return body
        raise DpdAuthError("DPD Poland authentication failed")

    async def async_get_parcels(self) -> list[dict[str, Any]]:
        """Return the authenticated receiver inbox."""
        body = await self._async_authorized("POST", f"{DPD_PL_API_URL}/mdupackageservices/api/v1/packages?userContext=RECEIVER", json={"alias": None, "sent": None})
        packages = body.get("packages")
        return [item for item in packages if isinstance(item, dict)] if isinstance(packages, list) else []

    async def async_get_parcel_detail(self, waybill: str) -> dict[str, Any]:
        """Return the detail record for one receiver parcel."""
        return await self._async_authorized("GET", f"{DPD_PL_API_URL}/mdupackageservices/api/v1/packages/{quote(waybill, safe='')}")
