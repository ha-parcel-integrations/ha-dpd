# Working in this repository

This is a Home Assistant custom integration for DPD parcel tracking.
Distributed via HACS; not part of HA core.

## Always consult HA developer documentation

Home Assistant's integration patterns evolve continuously. **Do not rely
on memory of past patterns** — fetch the canonical page before changing
a topic area, and check the developer blog before introducing anything
you only "know" from training data.

| When you change | Fetch first |
|---|---|
| Entity properties, naming, lifecycle, attributes | https://developers.home-assistant.io/docs/core/entity/ |
| Sensor specifics (state/device classes, units) | https://developers.home-assistant.io/docs/core/entity/sensor |
| Config flow, options flow, reauth, reconfigure | https://developers.home-assistant.io/docs/config_entries_config_flow_handler |
| DataUpdateCoordinator pattern | https://developers.home-assistant.io/docs/integration_fetching_data |
| Quality scale rules | https://developers.home-assistant.io/docs/core/integration-quality-scale |
| Diagnostics | https://developers.home-assistant.io/docs/integration_diagnostics |
| Translations | https://developers.home-assistant.io/docs/internationalization/core |
| Brand registration | https://developers.home-assistant.io/docs/creating_integration_brand |

### Recent developer-facing changes

Before introducing patterns you only know from training data, check:

- https://developers.home-assistant.io/blog — API deprecations, new
  patterns, breaking changes. Recent posts trump older recollection.
- https://github.com/home-assistant/architecture/discussions — design
  decisions in flight that have not made it into stable docs yet.

## What is already in place

The integration is aligned with the **silver** quality scale tier. Don't
re-propose these as improvements:

- `quality_scale: "silver"` in manifest, minimum HA version `2024.7.0`
- `ConfigEntry.runtime_data` (typed dataclass `DpdData`)
- `PARALLEL_UPDATES = 0` in `sensor.py`
- Per-parcel sensors self-remove via `async_remove(force_remove=True)`
- Coordinator logs warnings on unavailability (auth and connectivity)
- Reauth flow calls `async_reload` so new credentials propagate to the
  in-memory `DpdApiClient`
- Diagnostics handler in `diagnostics.py` with credential and PII
  redaction
- Tests cover config flow, sensor, coordinator, diagnostics, and
  setup/unload lifecycle (≥75% required for silver)
- `_unrecorded_attributes` on every summary sensor — parcel/shipment
  lists are kept out of the recorder long-term tables
- `_attr_attribution = "Data provided by DPD"` per entity

## What was deliberately skipped

- **`has_entity_name`** is *not* used on this integration. Switching to
  it would change friendly names for existing dashboards and automations.
  The user weighed this trade-off explicitly. Do not change it without
  asking.

## Repo-specific quirks

- The login flow goes through **Keycloak**: first a guest token, then a
  Consignee SSO exchange. The basic-auth client credentials are
  base64-decoded from a hardcoded blob in `const.py` that was lifted
  from DPD's Firebase Remote Config — that string is not a secret, but
  it does change occasionally, so re-fetch from the mobile app if calls
  start 401-ing for everyone.
- **Business Unit dropdown**: only `DPD-NL` is currently mapped in
  `BUSINESS_UNITS`. The setup code is BU-agnostic, but the tracking-URL
  pattern hardcodes `/nl/` in the path. If you add another BU, update
  the URL builder too.
- The "`PARCEL_HANDED` is the terminal hand-off status" intel comes
  from a community issue, not official docs. If a new status appears,
  the `DELIVERED_DESCRIPTION` const may need extending.
- `DpdEnRouteToParcelShopSensor` counts every non-delivered
  PARCELSHOP-destination parcel — DPD does not yet expose a distinct
  "arrived at ParcelShop" status, so we cannot tell *en route* apart
  from *awaiting collection*. Documented limitation, not a bug.

## Running tests

```
python -m pytest tests/ --cov=custom_components.dpd
```

Coverage must stay ≥75% (silver requirement). Run before committing.
