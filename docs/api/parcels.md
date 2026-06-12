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
| `status.status` | int | Numeric code: `0` = `ORDER_CREATED`, `5` = `DELIVERED`. Other values TBD. |
| `status.description` | string | Enum; only `ORDER_CREATED` and `DELIVERED` observed so far |
| `status.deliveryType` | string | `HOME` or `PARCELSHOP` |
| `status.eventDateAndTime` | string | Naive ISO 8601 timestamp of the most recent status event |
| `status.eventDateAndTimeZoneId` | string | IANA timezone identifier for `eventDateAndTime` |
| `status.city` / `status.countryCode` | string | Location of the most recent event |
| `status.homeDelivery` | bool | |
| `deliveryDate` | string | `YYYY-MM-DD`. Only observed on delivered parcels — TBD whether it appears for in-transit parcels as the planned date |
| `shipmentBUCode` | string | E.g. `"001"`, `"010"` — meaning TBD |
| `shipmentType` | string | `SHIPMENT` observed |

### What we still need

We have not yet observed a parcel in the in-transit states. Once one is captured we can confirm:

- The full set of `status.description` values (likely `IN_TRANSIT`, `OUT_FOR_DELIVERY`, `AT_PARCELSHOP`, etc.)
- Whether a planned-delivery date or window field exists for in-transit parcels
- The shipment fields that identify a target parcelshop (name/address)

Contributions welcome — see [issue #1](https://github.com/peternijssen/ha-dpd/issues/1).
