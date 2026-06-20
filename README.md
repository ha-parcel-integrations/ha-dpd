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

- Home Assistant 2024.7 or newer
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

### Setup parameters

| Field | Description |
|---|---|
| Email | The email address of your DPD consumer account (the one you use in the myDPD mobile app). |
| Password | The password for that account. Stored in the HA config entry and refreshed automatically when the integration triggers a re-authentication. |
| Country | The DPD business unit to query. Only **Netherlands** (`DPD-NL`) is mapped today; more land once contributors share parcel-payload samples. |

## Options

Click **Configure** on the integration card to change the delivered-parcels filter:

| Option | Description |
|---|---|
| Filter by | `Days` keeps delivered parcels visible for the last N days. `Number of parcels` keeps only the N most recent regardless of age. |
| Amount | The N used by the filter above. |

Changes take effect on the next refresh — no reload required.

## Removal

Standard HA removal applies: **Settings → Devices & Services →
DPD → ⋮ → Delete**. No DPD-side cleanup is needed; deleting the
config entry stops the polling. To revoke API access entirely, change
your DPD account password — the integration will trigger a re-auth
notification, which you can then ignore.

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

## Parcel status reference

`status` on every parcel is one of the canonical `ParcelStatus` values
below. Use these in your automations rather than DPD's raw description
strings — the raw value stays available on `raw_status` for power
users, and the [DPD status lifecycle](docs/api/parcels.md#status-lifecycle)
documents the source mapping in full.

| `status` | Meaning | DPD raw description that maps here |
|---|---|---|
| `registered` | DPD knows about the label but the parcel is not yet in transit | `ORDER_CREATED` |
| `in_transit` | Picked up; somewhere in DPD's network | `PARCEL_HANDED`, `IN_TRANSIT`, `AT_DELIVERY_CENTER` |
| `out_for_delivery` | On the delivery vehicle today | `PARCEL_OUT_FOR_DELIVERY` |
| `at_pickup_point` | Arrived at the ParcelShop, ready to collect | (not yet observed — DPD has no distinct "arrived at ParcelShop" status; ParcelShop-bound parcels surface as `out_for_delivery` on delivery day) |
| `delivered` | Handed over (mailbox, recipient, neighbour, picked up) | `DELIVERED` |
| `returning` | Failed delivery, on the way back to the sender | (not yet observed) |
| `problem` | Carrier reports an exception, intervention, or other issue | (not yet observed) |
| `unknown` | Raw description we have not mapped yet | anything else — logged once at info level so it can be added to the map |

This mapping is shared across the carriers: DHL and PostNL use the
same `ParcelStatus` values with their own raw-status mappings, so a
single event-driven automation can act on `status` regardless of
carrier.

## Events

The coordinator fires events on the HA event bus when something
interesting happens to a parcel, so automations can react without
polling per-parcel sensors.

| Event | When | Payload |
|---|---|---|
| `dpd_parcel_registered` | A new barcode appears in the active list | The full normalised parcel dict (`carrier`, `barcode`, `sender`, `status`, `raw_status`, `delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`, `pickup_point`, `url`, `raw`) |
| `dpd_parcel_status_changed` | A known barcode's canonical `status` value changes | Same payload plus `old_status` and `new_status` |

The coordinator suppresses events on the very first refresh after
start-up so you don't get a stampede of "registered" events for
parcels that were already in your account before HA started.

See [`examples/automations/`](examples/automations/) for ready-to-paste
event-driven automations, or the
[parcel aggregator](https://github.com/peternijssen/ha-parcel-aggregator)
for a carrier-agnostic re-emit layer that fires
`parcel_aggregator_parcel_*` events covering every installed carrier
in one go.

## Examples

The [`examples/`](examples/) folder ships ready-to-paste snippets for
both automations and dashboards. Highlights:

- [`examples/automations/notify_when_parcel_registered.yaml`](examples/automations/notify_when_parcel_registered.yaml) — push when DPD announces a new parcel.
- [`examples/automations/notify_when_out_for_delivery.yaml`](examples/automations/notify_when_out_for_delivery.yaml) — alert once per parcel when it's on the truck today; uses the FMP hour window when available.
- [`examples/automations/notify_when_at_parcelshop.yaml`](examples/automations/notify_when_at_parcelshop.yaml) — alert when a parcel is routed to a DPD ParcelShop.
- [`examples/automations/announce_delivery_window.yaml`](examples/automations/announce_delivery_window.yaml) — TTS announcement an hour before the next planned delivery.
- [`examples/dashboards/active_parcels_grid.yaml`](examples/dashboards/active_parcels_grid.yaml) — markdown card listing every active parcel with sender, canonical status and tracking link.
- [`examples/dashboards/summary_glance.yaml`](examples/dashboards/summary_glance.yaml) — compact glance row with the day-to-day counters.
- [`examples/dashboards/next_delivery_countdown.yaml`](examples/dashboards/next_delivery_countdown.yaml) — entities card showing the next expected delivery and details.

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
