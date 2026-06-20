# DPD Parcel Tracker

A custom Home Assistant integration that tracks your DPD shipments.

## Features

- Incoming and outgoing active-parcel count sensors
- Per-parcel sensor per active incoming shipment, with full status details as attributes
- Configurable delivered-parcels sensor (last N days, or N most recent)
- Automatic lifecycle management — per-parcel sensors are created and removed as parcels move through delivery
- Re-authentication support
- Country (business unit) selection during setup — Netherlands available today, more to come

## Requirements

- Home Assistant 2024.1 or newer
- A DPD account (the same credentials you use in the myDPD mobile app)

## Installation

### HACS (recommended)

1. Open HACS → **Integrations** → ⋮ → **Custom repositories**
2. Add this repository URL and select category **Integration**
3. Search for **DPD** and install it
4. Restart Home Assistant

### Manual

1. Copy the `dpd` folder into your `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **DPD**
3. Enter your DPD **email**, **password**, and pick your **country**
4. Choose how you want the **delivered parcels** sensor to filter (last N days, or N most recent)
5. Click **Submit**

The delivered-parcels filter can be changed later via **Settings → Devices & Services → DPD → Configure**. Changes take effect on the next refresh — no reload required.

## Sensors

The integration creates one device per DPD account, named
**`DPD (<your-email>)`**. With multiple accounts each gets its own
device named after its email. The entities below show the friendly-name
pattern; their entity IDs carry the same account slug:

| Friendly name pattern | Description |
|---|---|
| `DPD (account) Incoming parcels` | Number of active incoming parcels |
| `DPD (account) Parcel <barcode>` | Canonical status of a single active incoming shipment |
| `DPD (account) Next delivery` | Earliest expected delivery datetime. Uses Follow My Parcel's hour-window (`from` time) on the day a parcel is out for delivery; falls back to the calendar date at midnight for parcels not yet scheduled. |
| `DPD (account) En route to ParcelShop` | Active incoming parcels destined for a DPD ParcelShop pickup point |
| `DPD (account) Delivered parcels` | Recently delivered parcels (configurable window) |
| `DPD (account) Outgoing parcels` | Number of active outgoing parcels |

Every parcel exposed on a sensor attribute uses a carrier-agnostic shape:

| Key | Type | Meaning |
|---|---|---|
| `carrier` | string | `"DPD"` |
| `barcode` | string | Parcel tracking number |
| `sender` | string \| null | Sender name (e.g. webshop) |
| `status` | `ParcelStatus` | Canonical status — see the [status reference](#parcel-status-reference) |
| `raw_status` | string \| null | Original DPD status description (for power users) |
| `delivered` | bool | Whether the parcel has been delivered |
| `delivered_at` | ISO 8601 \| null | Delivery moment, if known |
| `planned_from` | ISO 8601 \| null | Expected delivery window start (Follow My Parcel hour on the day of delivery, else midnight on the planned date) |
| `planned_to` | ISO 8601 \| null | Expected delivery window end (Follow My Parcel hour, else 23:59:59 on the planned date) |
| `pickup` | bool | Destined for a ParcelShop rather than a home address |
| `pickup_point` | string \| null | Always `null` for now — DPD has not yet exposed the ParcelShop name field |
| `url` | string \| null | Deep link to the parcel's `www.dpdgroup.com/nl/mydpd/my-parcels` tracking page |
| `raw` | dict | The full original DPD shipment payload |

This is the same shape DHL and PostNL use, so the
[parcel aggregator](https://github.com/peternijssen/ha-parcel-aggregator)
and any cross-carrier dashboard can read parcels from all three
integrations the same way.

### Parcel statuses

DPD's `status.description` moves through six stages, mapped 1-to-1 to
the numeric `status.status` code. The integration recognises all of
them today; any new value DPD introduces is info-logged once per HA
session so it can be added to the catalogue.

| `status.status` | `status.description` | When it appears |
|---|---|---|
| `0` | `ORDER_CREATED` | Label printed; not yet handed to DPD |
| `1` | `PARCEL_HANDED` | Sender has handed the parcel to DPD |
| `2` | `IN_TRANSIT` | In DPD's network |
| `3` | `AT_DELIVERY_CENTER` | At the regional sorting hub the morning of delivery |
| `4` | `PARCEL_OUT_FOR_DELIVERY` | On the delivery vehicle today |
| `5` | `DELIVERED` | Terminal |

See [`docs/api/parcels.md`](docs/api/parcels.md#status-lifecycle) for the canonical reference.

### Delivery-time window

Every parcel exposes a planned delivery window as two top-level
attributes — `plannedDeliveryFrom` and `plannedDeliveryTo` — both
ISO 8601 strings with timezone offset, visible on the per-parcel
sensor and inside the `parcels` / `shipments` attribute of every
summary sensor.

| When | `plannedDeliveryFrom` / `plannedDeliveryTo` |
|---|---|
| On the day a parcel is out for delivery | The precise one-hour window DPD shows on its tracking page (e.g. `10:34` – `11:34`), fetched from the [Follow My Parcel](docs/api/fmp.md) sub-API. |
| Before the day of delivery | The full calendar day in the parcel's local timezone (`00:00:00` – `23:59:59` on the planned `deliveryDate`). |
| No delivery date known yet | `null` for both. |

`sensor.<account>_dpd_next_delivery` uses `plannedDeliveryFrom` as the
sort key, so it reports the actual hour the driver is expected — not
just midnight — on the day of delivery.

### Coming next

Blocked on additional data:

- A separate **awaiting-pickup** sensor — needs the DPD status value that indicates "parcel has arrived at the ParcelShop". Until that's mapped, all ParcelShop-bound parcels stay grouped in the en-route sensor.

See [issue #1](https://github.com/peternijssen/ha-dpd/issues/1) — extra data is very welcome.

## Debugging

To capture the raw DPD API response (useful when reporting a bug or helping map the shipment object structure), enable debug logging for the integration:

1. Add this to your `configuration.yaml`:
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.dpd: debug
   ```
2. Restart Home Assistant.
3. Wait for the next poll cycle (or reload the integration from **Settings → Devices & Services → DPD → ⋮ → Reload**).
4. Open **Settings → System → Logs**, filter for `dpd`, and copy the `DPD raw parcels payload: ...` line into your bug report or message to the maintainer.

The raw payload is only logged when there is at least one incoming or outgoing shipment.

## Troubleshooting

| Symptom | Likely cause |
|---------|--------------|
| `invalid_auth` error during setup | Wrong email or password |
| `cannot_connect` error during setup | DPD API is unreachable; check your network |
| Re-authentication prompt appears | DPD session expired and could not be refreshed silently; log in again |
| Sensors not updating | Check **Settings → System → Logs** for `dpd` entries |

## Related integrations

Tracking parcels from other Dutch carriers:

- [ha-dhl-nl](https://github.com/peternijssen/ha-dhl-nl) — DHL eCommerce NL parcel tracker
- [ha-postnl](https://github.com/arjenbos/ha-postnl) — PostNL parcel tracker
- [ha-parcel-aggregator](https://github.com/peternijssen/ha-parcel-aggregator) — rolls up counts and next-delivery timestamps from all installed carrier integrations into a single set of sensors

## Disclaimer

This is an independent, community-built project with no affiliation, endorsement, or connection to DPD or any of its subsidiaries. The DPD API used here is undocumented (reverse-engineered from the mobile app) and may change without notice.

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
