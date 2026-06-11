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
  "incomingShipments": [],
  "sendingShipments": [],
  "parcelTranslations": {
    "PARCEL_OF_NUMBERS_TODAY": "0 parcels",
    "PARCEL_OF_NUMBERS": "0 parcels"
  }
}
```

| Field | Description |
|-------|-------------|
| `incomingShipments` | Shipments addressed to the authenticated user. Object structure TBD. |
| `sendingShipments` | Shipments sent by the authenticated user. Object structure TBD. |
| `parcelTranslations` | Localised label strings; not currently consumed by the integration. |

> **TODO:** Capture a non-empty response and document the shipment object fields. This will unlock per-parcel sensors, the next-delivery sensor, and active-vs-delivered filtering.
