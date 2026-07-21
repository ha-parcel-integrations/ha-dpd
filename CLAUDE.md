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
| Diagnostics | https://developers.home-assistant.io/docs/core/integration/diagnostics |
| Translations | https://developers.home-assistant.io/docs/internationalization/core |

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
- Coordinator takes `config_entry=entry` so `self.config_entry` is
  available on the base class
- Per-parcel sensors are removed by the summary sensor
  (`DpdIncomingParcelsSensor`) via `entity_registry.async_remove(entity_id)`
  when a barcode drops out of the coordinator data. The earlier
  self-remove pattern raced with coordinator-listener cleanup and left
  ghost entities behind — do not revert.
- Reauth flow uses `async_update_reload_and_abort` (one helper call
  instead of update + reload + abort)
- `aiohttp.ClientError` is intentionally not caught in the coordinator —
  `DataUpdateCoordinator` wraps it automatically
- Diagnostics handler in `diagnostics.py` with credential and PII
  redaction
- Tests cover config flow, sensor, coordinator (incl. event firing),
  diagnostics, FMP delivery-window, and setup/unload lifecycle
- `_unrecorded_attributes` on every summary sensor — parcel lists are
  kept out of the recorder long-term tables
- `_attr_attribution = "Data provided by DPD"` per entity

### Adopted in 2.0.0 (do not refactor away)

- **Canonical `ParcelStatus` enum** in `const.py` — shared with DHL,
  PostNL and the parcel aggregator. `normalize_parcel` maps the raw
  DPD `status.description` via `map_parcel_status` and reports
  `ParcelStatus.UNKNOWN` (with one-shot info log) for anything not
  yet in `_DESCRIPTION_MAP`. The original DPD description lives on
  `raw_status`; do not re-introduce it on `status`.
- **Events:** the coordinator fires `dpd_parcel_registered`,
  `dpd_parcel_status_changed`, `dpd_parcel_delivered` and
  `dpd_parcel_delivery_time_changed` on the HA event bus. Events are
  suppressed on the very first refresh so we do not flood users with
  "registered" events for parcels that already existed.
  ``delivery_time_changed`` only fires when at least one of
  ``planned_from`` / ``planned_to`` ends up with a non-null value that
  differs from the previous one — ``value → null`` drops the ETA and is
  intentionally silent (carrier just lost the window; not worth a
  notification). Incoming events run over the **active + delivered** set
  combined (same trick as outgoing), so the terminal hop is visible: a
  change **to** `ParcelStatus.DELIVERED` fires only `_delivered` (never
  also `_status_changed`), a barcode first seen already-delivered fires
  nothing, and `registered` only fires for not-yet-delivered new
  barcodes. `_known_state` / `_known_delivery_times` track the combined
  set.
- **`has_entity_name = True`** on every entity, with `translation_key`
  routing names through `strings.json` and the language files. Drop
  `_attr_name` is the rule — translations are the source of truth.
- **Translated unit-of-measurement** (`entity.sensor.<key>.unit_of_measurement`
  in strings/translations). `_attr_native_unit_of_measurement` is
  intentionally absent. Every summary sensor uses the single `parcels`
  unit ("pakketten" in Dutch); the old `shipments` / `zendingen` split
  is gone.
- **`icons.json`** holds all sensor icons via the `translation_key`. Do
  not re-introduce `_attr_icon` on the sensor classes.
- **Device name pattern**: `"DPD (<email>)"`. Sensors auto-prefix with
  this, yielding friendly names like
  `DPD (account@example.com) Incoming parcels`.
- **Carrier-agnostic parcel shape** out of `normalize_parcel` —
  `carrier`, `barcode`, `sender`, `status` (enum), `raw_status`,
  `delivered`, `delivered_at`, `planned_from`, `planned_to`, `pickup`,
  `pickup_point`, `url`, plus the original DPD payload preserved under
  `raw`. (Extended in 2.1.0 with `receiver`, `weight`, `dimensions` —
  see below.) Sensors read from these top-level keys; the FMP-window
  and raw description handling lives only inside the coordinator.

### Adopted in 2.1.0 (do not refactor away)

- **Carrier-agnostic `receiver`, `weight`, `dimensions`** on every
  parcel, lazily filled from the per-parcel detail endpoint
  (`/v10/parcels/details/{n}`) via `_detail_cache`. The cache is keyed
  by barcode and lasts the lifetime of the integration, so the detail
  call fires at most once per parcel. A **failed** detail call is cached
  as `{"_failed": True, "_status_description": ...}` (not retried every
  poll) and retried once the parcel's `status.description` moves — one
  DPD hiccup must not mean missing receiver/weight until an HA restart. `dimensions` carries the raw
  float `length` / `width` / `height` plus a pre-formatted `text`
  field (`"L x W x H cm"`, integer values, lowercase `x`). The same
  `weight` and `dimensions` are also injected onto `raw` so power
  users can read them via the original payload — the list endpoint
  never populates those keys, so the addition is non-destructive.
- **Configurable refresh interval** via the options flow
  (`CONF_REFRESH_INTERVAL`; 15, 30, 60, 120 or 240 minutes; default 30).
  The form is split into `delivered` and `polling` sections via
  `data_entry_flow.section`. **Deliberate divergence** from the
  `ha-integration-knowledge` skill rule "polling intervals are NOT
  user-configurable": that rule targets HA Core integrations; this is a
  HACS integration where a user-tunable poll cadence is a wanted feature.
  Do not "fix" this to match the core rule.
- **No `entry.add_update_listener`** — the OptionsFlow calls
  `self.hass.config_entries.async_schedule_reload(entry.entry_id)` on
  submit so a changed refresh interval takes effect immediately. Reauth
  still reloads via `async_update_reload_and_abort` (that is correct and
  unrelated); the reauth-confirm step guards with `async_set_unique_id`
  + `_abort_if_unique_id_mismatch` so entering a *different* DPD
  account's credentials aborts instead of rebinding the entry. Combining an update listener with a reload-on-update flow
  is logged as a deprecation today and becomes an error in HA 2026.12+ —
  see the
  [config_entry_listener deprecation](https://developers.home-assistant.io/blog/2026/05/07/config-entry-listener-together-with-reloading-methods/).
- **Auth-tier 5xx surfaces as `ConfigEntryNotReady`** — `api.py` raises
  `DpdApiError(status_code)` before parsing the response when Keycloak
  returns a non-JSON 5xx page; `__init__.py` translates that to
  `ConfigEntryNotReady` so HA retries with backoff instead of crashing
  on `orjson.JSONDecodeError` or pushing the user into reauth.
- **First refresh runs in `__init__.py`, before `async_forward_entry_setups`**
  — `async_setup_entry` awaits `coordinator.async_config_entry_first_refresh()`
  before forwarding (not in the `sensor.py` platform). Raising
  `ConfigEntryNotReady` from a *forwarded* platform is too late for HA to
  catch — it logs a warning and half-sets-up the entry. Doing the first
  refresh here lets a transient fetch failure fail the whole entry so HA
  retries with backoff. Do not move it back into a platform.

### Adopted in 2.3.0 — history (do not refactor away)

- **Per-parcel `history`** — a new top-level canonical field (alongside
  `status`, `weight`, …): an ordered list (oldest → newest) of
  `{timestamp, status, raw_status}` events, capped to the most recent
  `HISTORY_MAX_EVENTS` (20). Built by `build_history` in
  `coordinator.py` from the detail endpoint's `parcelEvents`
  (`{date, time, eventType, eventTypeText}`). `timestamp` = `date` + `T`
  + `time`; `status` maps from the stable `eventType`; `raw_status` =
  `eventTypeText`. Kept identical across DHL / DPD / PostNL; top-level
  (not under `raw`) so it survives the aggregator's `strip_raw()`.
- **Opt-in, default OFF.** Options-flow boolean `CONF_INCLUDE_HISTORY`
  in its own `history` section, `async_schedule_reload` on submit (same
  pattern as `CONF_REFRESH_INTERVAL`). When off, `history` is `None` —
  the key is never omitted.
- **No new endpoint — reuse the detail call.** `_enrich_detail_cache`
  already fetches per-parcel detail for receiver/weight/dimensions; it
  now also keeps `parcelEvents`. The cache is **lifetime-per-barcode**
  for the immutable fields, but history **grows** on a status change, so
  when the option is on the cache stores `_status_description` and
  refetches the detail when a barcode's `status.description` moves. With
  the option off, the cache is never refetched (original behaviour). Do
  not collapse this back into "fetch once, forever".
- **Per-event status** maps from `eventType` via `_EVENT_TYPE_MAP` +
  `map_event_status` (NOT the `lang`-dependent `eventTypeText`). Unmapped
  codes → `null` (history) + a one-shot **warning**. The codes are DPD
  "Geo Event codes"; the full **68-code GSMT reference** (and which we
  map vs deliberately skip) lives in `docs/api/parcels.md` (local-only).
  We map only the subset a consumer parcel realistically emits — `CC*`
  customs, `PK*`/`CR*` sender-side and `MT*`/`QR*`/`MIDL*` contact codes
  are intentionally left unmapped; map on demand when feature B surfaces
  one. The parcelshop/PUDO codes (`DO*`, `DEHD*`, incl. `DODEI` →
  `at_pickup_point`) are mapped but **not yet confirmed** in real consumer
  `parcelEvents`.
- **Feature B — unknown-status warnings.** Both the parcel
  `status.description` (`log_unknown_descriptions`) and the history
  `eventType` (`map_event_status`) log **once per distinct unmapped
  value** at **WARNING** level with a copy-paste `issues/new` link
  (`_NEW_ISSUE_URL`). Replaced the old terse info log. Two parallel
  one-shot sets: `_unknown_descriptions_logged`,
  `_unknown_event_types_logged`.
- **Recorder:** `history` is in `_unrecorded_attributes` on
  `DpdParcelSensor`. Summary sensors already keep the whole parcel list
  out of the recorder via the `parcels` attribute.

### Adopted in 2.4.0 — device triggers + refresh button (do not refactor away)

- **`device_id` on every fired event.** `_fire_change_events` resolves the
  account's device id once (cached in `self._cached_device_id`, looked up
  via `dr.async_entries_for_config_entry`) and adds `device_id` to all
  three event payloads. Stays `None` until the device exists, which is
  fine — events are suppressed on the first refresh anyway. This is the
  key that lets device triggers filter per-account.
- **`device_trigger.py`** exposes the three bus events
  (`parcel_registered` / `parcel_status_changed` /
  `parcel_delivery_time_changed`) as no-code device triggers, delegating
  to `homeassistant.components.homeassistant.triggers.event` with
  `CONF_EVENT_DATA={device_id: ...}`. Trigger-type names live under
  `device_automation.trigger_type` in strings/translations.
- **Refresh `button`** (`Platform.BUTTON` in `PLATFORMS`, `button.py`).
  One `DpdRefreshButton` per account, unique_id `{entry_id}_refresh`,
  `translation_key="refresh"`. `async_press` calls
  `async_request_refresh()` on the (single) coordinator. Lands on the
  same `DPD (<email>)` device.
- **Sensor cleanup is now sensor-scoped.** The setup-time stale-entity
  loop in `sensor.py` filters on `entity_entry.domain == "sensor"` before
  treating a `{entry_id}_*` unique_id as a per-parcel barcode. Without
  this guard it deletes the refresh button (`{entry_id}_refresh`) on every
  setup. Do not drop the domain check.
- **Diagnostic `last_update` sensor** (`DpdLastUpdateSensor`, unique_id
  `{entry_id}_last_update`, `EntityCategory.DIAGNOSTIC`, device class
  TIMESTAMP). Reads `coordinator.last_success_time`, stamped with
  `datetime.now(timezone.utc)` at the end of every successful
  `_async_update_data`. Lets users alert on a silently stale integration.
  **Must be in `non_parcel_unique_ids`** in `sensor.py` — it is a sensor
  whose unique_id starts with `{entry_id}_`, so without the exclusion the
  setup cleanup loop deletes it as a stale parcel.
- **Deliveries `calendar`** (`Platform.CALENDAR` in `PLATFORMS`,
  `calendar.py`). One `DpdDeliveriesCalendar` per account, unique_id
  `{entry_id}_deliveries`, `translation_key="deliveries"`. Read-only view
  over `coordinator.data["incoming_active"]` — **no extra API calls**, so
  it is enabled by default (no options toggle). One `CalendarEvent` per
  active incoming parcel with a `planned_from`; `end` is `planned_to` or
  `planned_from + 1h`. `event` returns the soonest event whose `end >
  dt_util.now()`. Summary = sender (falls back to barcode); pickup parcels
  set `location`. A combined cross-carrier calendar lives in the
  **aggregator**, not here.
- **README stays lean** (see suite README house style): no `## Buttons`
  or `## Device triggers` sections; the device-trigger option is a single
  sentence folded into **Events**. The button and calendar are not
  documented in the README at all (discoverable in the HA UI). CLAUDE.md
  still documents everything.

### Adopted in 2.5.0 — outgoing delivered parcels (do not refactor away)

- **`DpdOutgoingDeliveredParcelsSensor`** (`{entry_id}_outgoing_delivered_parcels`,
  `translation_key="outgoing_delivered_parcels"`) — the delivered
  counterpart of `DpdOutgoingParcelsSensor`, mirroring how incoming has
  both an active and a delivered sensor. Reads a new
  `coordinator.data["outgoing_delivered"]` bucket, sorted by `delivered_at`
  descending. Brings DPD in line with PostNL and DHL, which both expose
  `outgoing_delivered_parcels`.
- **`_async_update_data` now splits `sendingShipments` four ways** — before
  2.5.0 delivered outgoing shipments were dropped; now
  `filter_delivered_shipments(outgoing)` goes through the shared
  `_apply_delivered_filter` (same days/count option as incoming delivered)
  into `outgoing_delivered`. `_enrich_detail_cache` receives
  `outgoing_active + outgoing_delivered` so delivered outgoing parcels get
  the same receiver/weight/dimensions/history enrichment.
- **Must be in `non_parcel_unique_ids`** in `sensor.py` (same reason as the
  other summary sensors).
- **Returns** — DPD splits server-side into `incomingShipments` /
  `sendingShipments`, so a return the account holder ships back lands in
  `sendingShipments` and flows into the outgoing sensors automatically. No
  `isReturn`-style filtering is needed here (unlike DHL, whose sent-shipments
  endpoint is empty for consumers). See the suite memory
  `returns_outgoing_parity`.

### Adopted after 2.5.0 — outgoing events (do not refactor away)

- **Two outgoing events fire from `DpdCoordinator`**:
  `dpd_outgoing_parcel_status_changed` and `dpd_outgoing_parcel_delivered`,
  via `_fire_outgoing_change_events`, over the combined
  `outgoing_active + outgoing_delivered` set (so a hop from in-transit to
  delivered is visible in one set). State tracked in
  `_known_outgoing_state` (barcode → ParcelStatus), `None` on the first
  refresh for the same suppression reason as `_known_state`.
- **`delivered` takes precedence over `status_changed`** for the terminal
  transition: a change **to** `ParcelStatus.DELIVERED` fires only
  `_delivered`, every other change fires only `_status_changed`. There is
  **no outgoing `registered` and no outgoing `delivery_time_changed`** — out
  of scope. An already-delivered sent shipment never fires (status
  unchanged). Both events carry `device_id` and are wired into
  `device_trigger.py` with labels under `device_automation.trigger_type` in
  strings/translations. Mirrors DHL exactly (kept identical suite-wide).

## Planned for the next major bump

- **Exception translations** (Gold-tier rule). `UpdateFailed(f"...")`
  still uses f-strings; the Gold push will move to `translation_key` +
  `translation_placeholders`.
- **Populated `pickup_point` field.** Blocked on DPD exposing the
  ParcelShop name/address. The myDPD app (3.78.26) has a `pudoDetail`
  block, so it may be derivable from the detail endpoint — needs a real
  parcelshop parcel to confirm the field shape.

## Repo-specific quirks

- The login flow goes through **Keycloak**: first a guest token, then a
  Consignee SSO exchange. The basic-auth client credentials are
  base64-decoded from a hardcoded blob in `const.py` that was lifted
  from DPD's Firebase Remote Config — that string is not a secret, but
  it does change occasionally, so re-fetch from the mobile app if calls
  start 401-ing for everyone.
- **Business Unit dropdown**: only `DPD-NL` is currently mapped in
  `BUSINESS_UNITS`. The setup code is BU-agnostic, but the tracking-URL
  pattern (`_tracking_url` in `coordinator.py`) hardcodes `/nl/` in the
  path. If you add another BU, update the URL builder too. The user step's
  `description` (strings/translations) links a pre-filled "Add country"
  GitHub issue so users can request their BU.
- **`KNOWN_DESCRIPTIONS`** in `const.py` is the catalogue of all
  recognised DPD `status.description` values; `_DESCRIPTION_MAP` in
  `coordinator.py` is the source of truth for the ParcelStatus
  mapping. Both need updating when DPD introduces a new lifecycle
  stage — the integration warns about unknown descriptions once per HA
  session so they are easy to spot. `AVAILABLE_FOR_COLLECTION`,
  `RETURN_TO_SENDER` and `UNSUCCESSFUL_DELIVERY_ATTEMPTED` were taken
  from the myDPD app's own `parcel_status` taxonomy (app 3.78.26); the
  consumer app does **not** use the granular GSMT geo codes (`DODEI`
  etc.), so those are history-only and probably never appear in our feed.
- **`fmpDeliveryDateAndTime`** (under `raw`) is filled by the
  coordinator's per-parcel FMP fetch when DPD exposes a
  `FOLLOW_MY_PARCEL` action. The fetch is explicitly best-effort: any
  non-200 / missing token / network error returns `None` and the
  parcels poll keeps going. `planned_from` / `planned_to` on the
  normalised dict reflect the FMP hour window when present, otherwise
  the calendar-day window in the parcel's local timezone.
- **ParcelShop sensors** mirror DHL/PostNL: `DpdEnRouteToParcelShopSensor`
  counts `pickup` parcels whose `status != at_pickup_point` (still in
  transit), and `DpdAwaitingPickupSensor` counts `pickup` parcels with
  `status == at_pickup_point` (ready to collect, the
  `AVAILABLE_FOR_COLLECTION` description). The split was unblocked by
  finding `AVAILABLE_FOR_COLLECTION` in the myDPD app's `parcel_status`
  enum — confirm against a real parcelshop parcel if one ever appears.

## Shared conventions

Workflow, commit style, versioning and release notes live in
[`ha-parcel-integrations/.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md)
and are not repeated here. In short: single-line commit messages, semver, tags
without a `v` prefix, maintainer-only merges, user-facing release notes.

The structural baseline every carrier repo shares is the
[`ha-carrier-template`](https://github.com/ha-parcel-integrations/ha-carrier-template)
scaffold. This repo predates it; where the two differ, the template is usually the newer thinking.

## Running tests

```
python -m pytest tests/ --cov=custom_components.dpd
```

Coverage must stay **above 95%** (the silver `test-coverage` rule on
developers.home-assistant.io). Run before committing.
