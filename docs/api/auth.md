# Authentication

DPD's mobile app uses a three-step flow to obtain an API token. All three steps are required for every fresh login; there is no documented refresh-token endpoint, so the integration repeats the full flow whenever the API returns 401/403.

## Step 1 — Keycloak login

Exchange the user's email and password for a Keycloak access token.

**URL:** `POST https://login.dpdgroup.com/auth/realms/login/protocol/openid-connect/token`
**Content-Type:** `application/x-www-form-urlencoded`

### Body

| Field | Value |
|-------|-------|
| `client_id` | `MOBILE-APP-PROD` |
| `grant_type` | `password` |
| `scope` | `openid` |
| `username` | DPD account email |
| `password` | DPD account password |

### Response

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "expires_in": 300,
  ...
}
```

Only the `access_token` field is used downstream.

## Step 2 — Mobile-app guest token

Exchange the hardcoded mobile-app client credentials for a short-lived guest token. This token is needed to call the `consignee-sso` endpoint in step 3.

**URL:** `POST https://www.dpdgroup.com/concept/webservice/oauth/token?grant_type=client_credentials`
**Headers:**

| Header | Value |
|--------|-------|
| `Authorization` | `Basic <base64(client_id:client_secret)>` |
| `Content-Type` | `application/json` |

The Basic-auth value is hardcoded in the mobile app (fetched from Firebase Remote Config). It decodes to `myDPD Mobile App:<secret>`.

### Response

```json
{
  "access_token": "...",
  ...
}
```

## Step 3 — Consignee SSO exchange

Exchange the Keycloak token (step 1) for a DPD user token, using the guest token (step 2) as the bearer.

**URL:** `POST https://www.dpdgroup.com/concept/webservice/users/login/consignee-sso?bu=<BU>`
**Headers:**

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <guest_token>` |
| `Content-Type` | `text/plain` |

**Body:** the Keycloak access token from step 1, sent as raw text (no quoting, no JSON wrapping).

| Query | Value |
|-------|-------|
| `bu` | Business unit, e.g. `DPD-NL` |

### Response

```json
{
  "access_token": "...",
  ...
}
```

The returned `access_token` is the **DPD API token** used as a Bearer for all subsequent API calls (e.g. [parcels](parcels.md)).
