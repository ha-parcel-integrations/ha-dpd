# Sensors

Full reference for all sensors provided by the DPD integration.

> **Friendly name pattern:** the integration creates one device per
> DPD account, named `DPD (<your-email>)`. Each sensor's friendly name
> is `<device-name> <entity-name>`, e.g.
> `DPD (account@example.com) Incoming parcels`.

> **Parcel shape:** every parcel exposed on a sensor attribute carries
> the carrier-agnostic top-level keys `carrier`, `barcode`, `sender`,
> `receiver`, `status` (the canonical
> [`ParcelStatus`](#parcel-status-reference) value), `raw_status` (the
> original DPD description string), `delivered`, `delivered_at`,
> `planned_from`, `planned_to`, `pickup`, `pickup_point`, `url`,
> `weight` (kg) and `dimensions` (`{height, width, length}` in cm) —
> the last two are lazily fetched from the per-parcel detail endpoint
> and stay `null` until that call has succeeded. The original DPD
> shipment payload lives under `raw`, with `weight` + `dimensions`
> also injected onto it.

## Incoming parcels

### `DPD (account) Incoming parcels`

Summary sensor showing how many parcels are currently on their way to you.

**State:** number of active incoming parcels (unit: `parcels`, translated as `pakketten` in Dutch HA).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of all active incoming parcel objects (normalised carrier-agnostic shape). Not recorded long-term to keep the recorder DB lean. |

### `DPD (account) Parcel <barcode>`

One sensor per active incoming shipment. Created automatically when a
new parcel appears and removed once it is delivered.

**State:** the canonical [`ParcelStatus`](#parcel-status-reference)
value (e.g. `out_for_delivery`, `in_transit`).

**Attributes:** the full normalised parcel dict — top-level fields plus
`raw_status` (the original DPD description) and `raw` (the full DPD
shipment payload, kept out of the recorder long-term).

### `DPD (account) Next delivery`

Earliest expected delivery datetime across all active incoming parcels.
Uses device class `timestamp` so Home Assistant treats it as a proper
datetime — useful for time-based automations.

On the day a parcel is out for delivery, the value is the **precise hour**
DPD's tracking page shows, fetched via the
[Follow My Parcel](api/fmp.md) sub-API. Before that day it is midnight
in the parcel's local timezone — useful for "delivery is today /
tomorrow" automations.

**State:** datetime of the next expected delivery, or `unavailable` if
no parcels have a known delivery time.

| Attribute | Description |
|-----------|-------------|
| `barcode` | Barcode of the parcel arriving soonest |
| `sender` | Name of the sender of that parcel |
| `receiver` | Recipient name of that parcel (lazy-fetched from the DPD detail endpoint; may be `null` on first refresh) |

### `DPD (account) En route to ParcelShop`

Active incoming parcels destined for a DPD ParcelShop pickup point.
DPD does not yet expose a distinct "arrived at ParcelShop" status, so
this sensor groups *both* in-transit-to-ParcelShop and
awaiting-collection parcels — see [Roadmap](#roadmap).

**State:** number of ParcelShop-bound parcels (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised ParcelShop-bound parcels |

### `DPD (account) Delivered parcels`

Recently delivered incoming parcels. The window is controlled by the
integration options (see [Options](#options)).

**State:** number of delivered parcels shown (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised delivered parcels |

---

## Outgoing parcels

### `DPD (account) Outgoing parcels`

Summary sensor showing how many parcels you have sent that are still
in transit. Shipments with raw description `DELIVERED` are excluded.
No per-shipment sensors are created — all data is available as
attributes on this single sensor.

**State:** number of active outgoing parcels (unit: `parcels`).

| Attribute | Description |
|-----------|-------------|
| `parcels` | List of normalised active outgoing parcels |

---

## Parcel status reference

`status` on every parcel is one of these canonical
[`ParcelStatus`](../custom_components/dpd/const.py) values. Use these
in automations rather than DPD's raw description strings —
`raw_status` keeps the original DPD value available for power users.

| `status` | Meaning | DPD raw description that maps here |
|---|---|---|
| `registered` | DPD knows about the label but the parcel is not yet in transit | `ORDER_CREATED` |
| `in_transit` | Picked up; somewhere in DPD's network | `PARCEL_HANDED`, `IN_TRANSIT`, `AT_DELIVERY_CENTER` |
| `out_for_delivery` | On the delivery vehicle today | `PARCEL_OUT_FOR_DELIVERY` |
| `at_pickup_point` | Arrived at the chosen ParcelShop, ready to collect | (not yet observed; ParcelShop-bound parcels surface as `out_for_delivery` on delivery day) |
| `delivered` | Handed over, mailbox, neighbour, or picked up | `DELIVERED` |
| `returning` | Failed delivery, on the way back | (not yet observed) |
| `problem` | Carrier reports an exception, intervention, or other issue | (not yet observed) |
| `unknown` | Raw description we have not mapped yet | anything else — logged once per HA session at info level |

---

## Events

The coordinator fires events on the HA event bus when something changes:

| Event | When | Payload |
|---|---|---|
| `dpd_parcel_registered` | A new barcode appears in the active list | Full normalised parcel dict |
| `dpd_parcel_status_changed` | A known barcode's canonical `status` changes | Normalised parcel dict plus `old_status` and `new_status` |

Events are suppressed on the very first refresh after start-up to
avoid a flood of "registered" events for parcels that already existed.

Because events fire on the canonical `status`, intra-`in_transit`
description churn (e.g. `PARCEL_HANDED` → `IN_TRANSIT` →
`AT_DELIVERY_CENTER`) yields **no** event — they all map to
`ParcelStatus.IN_TRANSIT`.

See [`examples/automations/`](../examples/automations/) for ready-to-paste
event-driven automations.

---

## Options

After setup, click **Configure** on the integration card to change the
delivered-parcels filter:

| Option | Description |
|--------|-------------|
| **Filter by** | `Days` — show parcels delivered in the last N days. `Number of parcels` — show the N most recent deliveries. |
| **Amount** | The number of days or parcels (1–365). Default: **7 days**. |

Changes take effect on the next data refresh without requiring a reload.

---

## Poll interval

Data is refreshed every **15 minutes**. You can trigger a manual refresh
from the integration's device page using the **Reload** option.

---

## Roadmap

DPD has not (yet) exposed the data needed for these refinements —
contributions on [issue #1](https://github.com/peternijssen/ha-dpd/issues/1)
are very welcome:

- A distinct `at_pickup_point` status — would split the en-route
  ParcelShop sensor into "in transit" and "awaiting collection".
- A real `pickup_point` value — DPD has not exposed the ParcelShop
  name/address field yet, so the field is always `null`.
- Additional business units — only `DPD-NL` is mapped today.

---

## Debug logging

Add the following to `configuration.yaml` to enable verbose logging:

```yaml
logger:
  logs:
    custom_components.dpd: debug
```

The raw parcels payload is logged at debug level whenever there is at
least one incoming or outgoing shipment — handy when reporting a bug or
helping map new status values.
