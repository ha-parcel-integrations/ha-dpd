# Follow My Parcel (FMP)

DPD exposes a separate sub-API that returns the **precise delivery
window** (`from` / `to` time range) for a parcel on the day it goes
out for delivery. The main [parcels](parcels.md) endpoint only carries
the calendar `deliveryDate`, so FMP is the only way to surface an
hour-window in Home Assistant.

The flow is two calls per parcel:

1. **Authenticate** with the parcel's `hashcode` to receive a short-lived FMP token.
2. **Fetch the FMP shipment detail** with that token to read `deliveryDateAndTime`.

The hashcode is per-parcel and per-window — it appears on the main
parcels response as soon as DPD has scheduled a delivery slot for the
parcel (typically the morning of delivery).

## Discovering the hashcode

Each shipment in the [parcels](parcels.md) response carries an
`availableActions` map. When the FMP window is ready, the
`FOLLOW_MY_PARCEL` action is present:

```json
{
  "parcelNumber": "01XXXXXXXXXXXX",
  "status": { "description": "PARCEL_OUT_FOR_DELIVERY", "status": 4, ... },
  "availableActions": {
    "FOLLOW_MY_PARCEL": [
      { "hashcode": "<long opaque string>" }
    ]
  }
}
```

Pluck `availableActions.FOLLOW_MY_PARCEL[0].hashcode`. Shipments
without this action are not (yet) FMP-eligible.

## Step 1 — Authenticate with a hashcode

Exchange the hashcode for an FMP access token. Requires a valid main
DPD API token (see [auth.md](auth.md)) in the `Authorization` header.

**URL:** `POST https://www.dpdgroup.com/concept/webservice/fmp/authenticate`
**Headers:**

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <dpd_token>` |
| `Content-Type` | `application/json` |

### Body

```json
{
  "authMethod": "HASHCODE",
  "credentials": "<hashcode from availableActions>"
}
```

### Response

```json
{
  "access_token": "<fmp_token>",
  ...
}
```

The `access_token` is the **FMP token**, scoped to this single parcel.
Use it in step 2.

## Step 2 — Fetch the FMP shipment detail

**URL:** `GET https://www.dpdgroup.com/concept/webservice/v3/fmp/shipment?lang=<LANG>`
**Headers:**

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <fmp_token>` |
| `Content-Type` | `application/json` |

| Query | Value |
|-------|-------|
| `lang` | Response language, e.g. `en` or `nl` |

### Response

Only one field matters today; the rest is largely a duplicate of the
shipment object from [parcels](parcels.md).

```json
{
  "deliveryDateAndTime": {
    "deliveryDate": "2026-06-17",
    "timeRange": {
      "from": "10:34:00",
      "to": "11:34:00"
    }
  }
}
```

| Field | Type | Notes |
|---|---|---|
| `deliveryDateAndTime.deliveryDate` | string | `YYYY-MM-DD`. Matches the calendar date from the main parcels response. |
| `deliveryDateAndTime.timeRange.from` | string | `HH:MM:SS` — start of the delivery window, in the parcel's local timezone (from the main parcels response's `status.eventDateAndTimeZoneId`). |
| `deliveryDateAndTime.timeRange.to` | string | `HH:MM:SS` — end of the delivery window. Usually 60 minutes after `from`. |

## How the integration uses it

After each parcels poll, the coordinator iterates the active incoming
shipments, plucks the FMP hashcode when present, and calls the
[`DpdApiClient.async_fmp_delivery_window`](../../custom_components/dpd/api.py)
helper. The returned `deliveryDateAndTime` is stored verbatim on the
shipment dict (as `fmpDeliveryDateAndTime`); `shipment_planned_dt`
prefers `timeRange.from` over the calendar-date midnight fallback when
present.

FMP calls are explicitly best-effort: any non-200, missing token, or
network failure is logged at debug level and the shipment keeps its
date-only `planned_dt`. The main parcels poll is never blocked.
