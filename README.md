# DPD Parcel Tracker

A custom Home Assistant integration that tracks your DPD shipments.

> **Status:** early development. The parcels endpoint is wired up but the parcel object structure is not yet fully known, so per-parcel and delivery-time sensors will be added once the response shape is mapped out.

## Features

- Incoming and outgoing parcel count sensors
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
4. Click **Submit**

## Sensors

| Entity | Description |
|--------|-------------|
| `sensor.<account>_dpd_incoming_parcels` | Number of incoming parcels; full list exposed as the `parcels` attribute |
| `sensor.<account>_dpd_outgoing_parcels` | Number of outgoing shipments; full list exposed as the `shipments` attribute |

More sensors (per-parcel status, next-delivery datetime, ServicePoint en-route/awaiting-pickup, delivered) will be added once the parcel object shape is mapped.

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

## Disclaimer

This is an independent, community-built project with no affiliation, endorsement, or connection to DPD or any of its subsidiaries. The DPD API used here is undocumented (reverse-engineered from the mobile app) and may change without notice.

## Contributing

Pull requests and issues are welcome. Please open an issue before submitting a large change.

## License

MIT
