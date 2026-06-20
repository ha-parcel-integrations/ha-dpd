# Parcels

Returns the user's incoming and outgoing shipments. Requires a [DPD API token](auth.md).

**URL:** `POST https://www.dpdgroup.com/concept/webservice/v7/parcels?bu=<BU>&lang=<LANG>`
**Headers:**

| Header | Value |
|--------|-------|
| `Authorization` | `Bearer <dpd_token>` |
| `Content-Type` | `application/json` |

| Query | Value |
|-------|-------|
| `bu` | Business unit, e.g. `DPD-NL` |
| `lang` | Response language, e.g. `en` or `nl` |

## Body

The endpoint is a `POST` rather than a `GET` because the client tells the server which parcels it already knows about (the `*Parcels` lists). The integration always sends empty lists, asking the server to return everything.

```json
{
  "incomingParcels": [],
  "sendingParcels": [],
  "confirmedParcels": null,
  "shipmentCollections": [],
  "confirmedShipmentCollections": null
}
```

## Response

```json
{
  "incomingShipments": [ /* shipment objects */ ],
  "sendingShipments":  [ /* shipment objects */ ],
  "parcelTranslations": {
    "PARCEL_OF_NUMBERS_TODAY": "0 parcels",
    "PARCEL_OF_NUMBERS": "0 parcels"
  }
}
```

| Field | Description |
|-------|-------------|
| `incomingShipments` | Shipments addressed to the authenticated user. |
| `sendingShipments` | Shipments sent by the authenticated user. |
| `parcelTranslations` | Localised label strings; not currently consumed by the integration. |

## Shipment object

```json
{
  "parcelNumber": "01XXXXXXXXXXXX",
  "shipmentId": "01XXXXXXXXXXXX",
  "shipmentOriginalCode": "B2CXXXXXXXXXXXXXXXXXXXXX",
  "senderName": "Ha-Ra GmbH",
  "status": {
    "status": 0,
    "description": "ORDER_CREATED",
    "deliveryType": "HOME",
    "eventDateAndTime": "2026-06-12T10:24:18",
    "eventDateAndTimeZoneId": "Europe/Amsterdam",
    "city": "Origin City",
    "countryCode": "NL",
    "homeDelivery": true
  },
  "deliveryDate": "2026-06-05",
  "fmpRedirectionPossibility": false,
  "refused": false,
  "shipmentBUCode": "001",
  "shipmentType": "SHIPMENT"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `parcelNumber` | string | Canonical barcode; used as the per-parcel sensor key |
| `shipmentId` | string | Appears identical to `parcelNumber` in samples seen |
| `shipmentOriginalCode` | string | Internal sender code; not always present |
| `senderName` | string | |
| `status.status` | int | Numeric code paired with `description`. See [status lifecycle](#status-lifecycle) below for the full mapping. |
| `status.description` | string | Enum. See [status lifecycle](#status-lifecycle). Unknown values are surfaced once per HA session at info-level so they can be added to the catalogue. |
| `status.deliveryType` | string | `HOME` or `PARCELSHOP` |
| `status.eventDateAndTime` | string | Naive ISO 8601 timestamp of the most recent status event |
| `status.eventDateAndTimeZoneId` | string | IANA timezone identifier for `eventDateAndTime` |
| `status.city` / `status.countryCode` | string | Location of the most recent event |
| `status.homeDelivery` | bool | |
| `deliveryDate` | string | `YYYY-MM-DD`. For active parcels this is the **planned** delivery date (no time component). For delivered parcels it is the actual delivery date. |
| `shipmentBUCode` | string | E.g. `"001"`, `"010"` — meaning TBD |
| `shipmentType` | string | `SHIPMENT` observed |

### Status lifecycle

The two fields move in lock-step through the same six stages, in the
order shown below. Numeric `status.status` increments from `0` to `5`;
`status.description` carries the human-readable label.

| `status.status` | `status.description` | When it appears |
|---|---|---|
| `0` | `ORDER_CREATED` | Label has been printed by the sender; parcel not yet handed to DPD. |
| `1` | `PARCEL_HANDED` | Sender has handed the parcel to DPD. |
| `2` | `IN_TRANSIT` | Parcel is moving through DPD's network. Confirmed via parcelHistory samples on 2026-06-16. |
| `3` | `AT_DELIVERY_CENTER` | Arrived at the regional sorting hub the morning of delivery. Still in-transit from a user perspective. |
| `4` | `PARCEL_OUT_FOR_DELIVERY` | On the delivery vehicle today. |
| `5` | `DELIVERED` | Terminal. |

Stages `3` and `4` come from the community ([issue #1](https://github.com/peternijssen/ha-dpd/issues/1));
the numeric mapping for them is inferred from the contiguous 0 → 5
progression we have observed. If you see a numeric code or description
not listed above, please open an issue — the integration logs
unknown descriptions once per HA session at info level so they are
easy to spot in **Settings → System → Logs**.

There is a **separate** higher-resolution data source for the planned
delivery window (`from` / `to` time range) of the day a parcel is
out for delivery — the
**[Follow My Parcel](fmp.md)** sub-API. The integration uses it to fill
in `planned_from` / `planned_to`; without FMP only the calendar date
is exposed.

### What we still need

Still TBD:

- The shipment fields that identify a target parcelshop (name/address) when `status.deliveryType == "PARCELSHOP"`.
- Whether DPD exposes a distinct "arrived at ParcelShop" status (today we cannot tell *en route* apart from *awaiting collection* for parcelshop-destined parcels).

Contributions welcome — see [issue #1](https://github.com/peternijssen/ha-dpd/issues/1).
