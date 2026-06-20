# Examples

Ready-to-paste Home Assistant snippets for the DPD integration.

| Folder | Contents |
|---|---|
| [`automations/`](automations/) | YAML automation blueprints — copy into your `automations.yaml` or paste into the Automation editor's **raw editor** mode. |
| [`dashboards/`](dashboards/) | Lovelace dashboard card snippets — paste into the YAML editor of any card. |

The examples assume one DPD account. With multiple accounts your
entity IDs carry the account in the slug
(e.g. `sensor.dpd_account_example_com_incoming_parcels`); adjust the
references accordingly.

## Events used in the examples

From 2.0.0 onwards the DPD coordinator fires:

| Event | When | Payload |
|---|---|---|
| `dpd_parcel_registered` | A new barcode appears in the active list | The full normalised parcel dict (`carrier`, `barcode`, `sender`, `status`, `raw_status`, `delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`, `pickup_point`, `url`, `raw`) |
| `dpd_parcel_status_changed` | A known barcode's normalised `status` transitions (e.g. `in_transit` → `out_for_delivery`) | Same as above plus `old_status` and `new_status` (both `ParcelStatus` enum values) |

The integration suppresses events on the very first refresh after
start-up to avoid a stampede of *"registered"* events for parcels that
were already there before HA started.

For a carrier-agnostic version of these automations that fires for any
of your installed carriers in one go, see the
[parcel aggregator examples](https://github.com/peternijssen/ha-parcel-aggregator/tree/main/examples).
