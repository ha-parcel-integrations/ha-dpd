# Working in this repository

Home Assistant custom integration for DPD parcel tracking. Distributed via HACS;
not part of HA core. **Silver** quality tier, minimum HA `2024.7.0`.

## Shared conventions — fetch when relevant

Suite-wide rules live in
[`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are **not** repeated here. Don't fetch it every session — fetch it **before**
you act in one of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change first-refresh or unmapped-status logging | *Parcel contract* (this repo implements it; below is only where DPD deviates) |
| consider "fixing" a lint/pattern the skill flags (poll interval, inline client, sync requests) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwire, kept inline on purpose:** the first refresh runs in
`__init__.py` *before* `async_forward_entry_setups`, never in a platform — from a
forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
entry. Runtime-only; the tests don't catch a regression here.

## Load-bearing DPD decisions — do not refactor away

Each line is a guardrail: the rule, then why it must stay. The code holds the
detail; this list stops you re-breaking a past fix.

**Auth & setup**
- **Keycloak login**: guest token, then a Consignee SSO exchange. The basic-auth
  client credentials are base64-decoded from a hardcoded blob in `const.py`
  lifted from DPD's Firebase Remote Config — not a secret, but it changes
  occasionally; re-fetch from the mobile app if calls start 401-ing for everyone.
- **Auth-tier 5xx → `ConfigEntryNotReady`**: `api.py` raises
  `DpdApiError(status_code)` before parsing when Keycloak returns a non-JSON 5xx
  page; `__init__.py` maps it to `ConfigEntryNotReady` (retry with backoff)
  instead of crashing on `JSONDecodeError` or forcing reauth.
- **Reauth** uses `async_update_reload_and_abort`; the confirm step guards with
  `async_set_unique_id` + `_abort_if_unique_id_mismatch` so a *different*
  account's credentials abort instead of rebinding.
- **Options flow** has no `entry.add_update_listener` — `async_schedule_reload`
  on submit. `CONF_REFRESH_INTERVAL` = 15/30/60/120/240 min, default 30.
- `aiohttp.ClientError` is not caught in the coordinator (wrapped
  automatically). Config: `ConfigEntry.runtime_data` (`DpdData`),
  `PARALLEL_UPDATES = 0`, coordinator takes `config_entry=entry`.

**Business unit**
- **Only `DPD-NL` is mapped** in `BUSINESS_UNITS`. Setup is BU-agnostic but the
  tracking-URL builder (`_tracking_url`) hardcodes `/nl/` — update it too if you
  add a BU. The user step's `description` links a pre-filled "Add country" issue.

**Status mapping**
- `normalize_parcel` maps DPD's `status.description` via `map_parcel_status` /
  `_DESCRIPTION_MAP`; the raw description lives on `raw_status`, never `status`.
  Unmapped → `ParcelStatus.UNKNOWN`. `KNOWN_DESCRIPTIONS` (`const.py`) is the
  recognised-value catalogue; both it and `_DESCRIPTION_MAP` need updating on a
  new DPD lifecycle stage. `AVAILABLE_FOR_COLLECTION` / `RETURN_TO_SENDER` /
  `UNSUCCESSFUL_DELIVERY_ATTEMPTED` came from the myDPD app taxonomy (3.78.26).
- **ParcelShop sensors**: `DpdEnRouteToParcelShopSensor` counts `pickup` parcels
  with `status != at_pickup_point`; `DpdAwaitingPickupSensor` counts
  `status == at_pickup_point` (the `AVAILABLE_FOR_COLLECTION` description) —
  confirm against a real parcelshop parcel if one appears.
- Unknown-status warnings fire once per distinct value from both
  `log_unknown_descriptions` and `map_event_status`, with an `issues/new` link
  (`_NEW_ISSUE_URL`); sets `_unknown_descriptions_logged` /
  `_unknown_event_types_logged`.

**Detail cache, FMP window, receiver/weight/dimensions**
- **`_detail_cache`** (keyed by barcode, integration-lifetime) lazily fills
  `receiver` / `weight` / `dimensions` from the detail endpoint
  (`/v10/parcels/details/{n}`) — at most one call per parcel. A **failed** call
  is cached as `{"_failed": True, "_status_description": …}` (not retried every
  poll) and retried once the parcel's `status.description` moves — one hiccup
  must not mean missing data until restart. `weight`/`dimensions` (with a
  `"L x W x H cm"` `text`) are also injected onto `raw` (list endpoint never
  populates them — non-destructive).
- **`fmpDeliveryDateAndTime`** (under `raw`) is filled by a per-parcel FMP fetch
  when DPD exposes a `FOLLOW_MY_PARCEL` action — best-effort (any failure →
  `None`, poll continues). `planned_from`/`planned_to` reflect the FMP hour
  window when present, else the calendar-day window in the parcel's local tz.

**History (opt-in, default OFF — `CONF_INCLUDE_HISTORY`)**
- Top-level `history`: ordered `{timestamp, status, raw_status}`, capped at
  `HISTORY_MAX_EVENTS` (20). Built by `build_history` from the detail endpoint's
  `parcelEvents`; `status` maps from the stable `eventType`, `raw_status` =
  `eventTypeText`. Top-level so it survives `strip_raw()`; `None` when off.
- **No new endpoint** — reuse the detail call. With the option on, the cache
  stores `_status_description` and refetches detail when a barcode's status moves
  (history grows on a status change); with it off the cache is never refetched.
  Do not collapse back into "fetch once, forever".
- Per-event status maps from `eventType` via `_EVENT_TYPE_MAP` +
  `map_event_status` (NOT the `lang`-dependent `eventTypeText`). We map only the
  consumer-realistic subset of DPD's 68 "Geo Event" codes (`CC*`/`PK*`/`CR*`/
  `MT*`/`QR*`/`MIDL*` intentionally unmapped; PUDO `DO*`/`DEHD*` mapped but
  unconfirmed). Full reference in `docs/api/parcels.md` (local-only).

**Outgoing (own shipments + returns) & events**
- DPD splits server-side into `incomingShipments` / `sendingShipments`, so a
  return the account ships back lands in `sendingShipments` and flows into the
  outgoing sensors automatically — **no `isReturn` filtering** needed here
  (unlike DHL).
- `_async_update_data` splits `sendingShipments` into active + delivered:
  `filter_delivered_shipments` → `outgoing_delivered` (via the shared
  `_apply_delivered_filter`), feeding `DpdOutgoingDeliveredParcelsSensor`
  (`{entry_id}_outgoing_delivered_parcels`). `_enrich_detail_cache` gets
  `outgoing_active + outgoing_delivered` too.
- Incoming events run over **active + delivered** combined (terminal hop visible:
  change **to** DELIVERED fires only `_delivered`; already-delivered fires
  nothing; `registered` only for not-yet-delivered new barcodes).
  `delivery_time_changed` only on a non-null `planned_*` that differs. Outgoing
  events (`dpd_outgoing_parcel_status_changed` / `_outgoing_parcel_delivered`)
  run over `outgoing_active + outgoing_delivered`; `delivered` wins the terminal
  hop; **no** outgoing `registered` / `delivery_time_changed`. State in
  `_known_state` / `_known_delivery_times` / `_known_outgoing_state`.
- `device_id` on every payload (`_cached_device_id`); `device_trigger.py`
  exposes all bus events as no-code triggers under
  `device_automation.trigger_type`.

**Entities & surfaces**
- `has_entity_name = True` + `translation_key` everywhere (no `_attr_name`);
  icons in `icons.json`, translated unit-of-measurement — every summary sensor
  uses the single `parcels` unit (the old `shipments`/`zendingen` split is gone).
  Device name `"DPD (<email>)"`; `_attr_attribution`; `_unrecorded_attributes`
  keeps parcel lists (and `history`) out of the recorder.
- **Per-parcel sensors are removed by the summary sensor** via
  `entity_registry.async_remove` (the old self-remove raced and left ghosts).
  **Setup cleanup is sensor-scoped** (filter `domain == "sensor"` before treating
  `{entry_id}_*` as a barcode, else it deletes the button); all non-parcel
  `{entry_id}_*` sensors (`_refresh`, `_last_update`,
  `_outgoing_delivered_parcels`, …) **must** stay in `non_parcel_unique_ids`.
- **Refresh `button`** (`{entry_id}_refresh`), **diagnostic `last_update` sensor**
  (reads `coordinator.last_success_time`), **deliveries `calendar`**
  (`{entry_id}_deliveries`, read-only over `incoming_active`, no extra API calls,
  enabled by default).

## Planned / skipped

- **Planned (next major)**: exception translations (`UpdateFailed` f-strings →
  `translation_key` + placeholders); populated `pickup_point` — blocked on DPD
  exposing the ParcelShop name/address (the myDPD `pudoDetail` block may derive
  it; needs a real parcelshop parcel).

## Running tests

```
python -m pytest tests/ --cov=custom_components.dpd
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing.
