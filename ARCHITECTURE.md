# Architecture

How `ha-dpd` is built and why it is built that way. `CLAUDE.md` is the short
list of things not to get wrong; this file is the reasoning and the evidence
trail behind it. API mechanics — endpoints, parameters, status vocabularies —
live in the private `carrier-research/dpd/` and are never copied here.

DPD is the largest integration in the suite because it is not one backend but
three: a shared myDPD/Keycloak stack serving the Netherlands and 15 more
business units, a wholly separate SOAP stack for Germany, and a separate OAuth
stack for Poland.

## Project layout

```
custom_components/dpd/
├── __init__.py          setup, transport construction, first refresh
├── api.py               general transport: myDPD/Keycloak HTTP client
├── const.py             domain, BUSINESS_UNITS, overrides, ParcelStatus, CAPABILITIES_BY_VARIANT
├── coordinator.py       fetch dispatch, caches, filtering, event firing
├── parcels.py           shared pure helpers (filters, sort) — no I/O, no HA objects
├── config_flow.py       user/reauth/options flows, BU + country routing
├── sensor.py            summary, per-parcel, ParcelShop and diagnostic sensors
├── button.py            refresh button
├── calendar.py          read-only deliveries calendar over incoming_active
├── device.py            device registry helpers
├── device_trigger.py    device automation triggers
├── diagnostics.py       redacted diagnostics incl. the "polling" block
└── countries/
    ├── general/         status map + normalize_parcel for the myDPD path
    ├── de/              Germany: SOAP session + status derivation + normalize
    └── pl/              Poland: OAuth session + status derivation + normalize
```

`countries/general/`, `countries/de/` and `countries/pl/` each own a
`normalize_parcel*` and a status mapper. `parcels.py` keeps only what all three
share, and stays free of I/O and HA objects so it is unit-testable without Home
Assistant.

## Transports and dispatch

There is **one dispatch point**, in `DpdCoordinator._async_update_data`:

```
if self._de_session is not None:     ->  _async_fetch_de()
elif self._pl_session is not None:   ->  _async_fetch_pl()
else:                                ->  the general/BU path via DpdApiClient
```

`__init__.py` decides which of the three to construct from `CONF_COUNTRY`
(`COUNTRY_DE`, `COUNTRY_PL`, else the general path) and
passes exactly one. Everything past that dispatch — sorting, delivered
filtering, event firing, the dynamic-polling recompute, entity population — is
shared. A country transport therefore never touches `api.py` or the general
fetch path, and adding one does not change the shared code.

| Transport | Session/client | Protocol | Status mapper | Normalizer |
|---|---|---|---|---|
| General (NL + BUs, UK) | `DpdApiClient` (`api.py`) | Keycloak + JSON REST | `_DESCRIPTION_MAP` | `normalize_parcel` |
| Germany | `DpdDeSession` (`countries/de/session.py`) | ASP.NET SOAP | `map_parcel_status_de` | `normalize_parcel_de` |
| Poland | `DpdPlSession` (`countries/pl/session.py`) | public-client OAuth + JSON | `map_parcel_status_pl` | `normalize_parcel_pl` |

## Key design decisions

### Business units share one backend — with three exceptions

`BUSINESS_UNITS` in `const.py` holds 17 entries. NL plus 14 more (`DPD-AR`,
`DPD-BE`, `DPD-HR`, `DPD-CZ`, `DPD-EE`, `DPD-FR`, `DPD-HU`, `BRT`, `DPD-LV`,
`DPD-LT`, `DPD-LU`, `CHR-PT`, `DPD-SK`, `DPD-SI`) were confirmed (2026-08-13)
to share NL's account backend and auth by **two independent lines of
evidence** — the myDPD web preferences dropdown *and* the shared myDPD Android
app's own embedded BU list (see `dpd.md`, Log 2026-08-11, in the private
research repo). A non-NL `v7/parcels` list/detail payload shape is still
**unconfirmed by a live capture**; treat any field mismatch as expected until
one arrives. `KNOWN_DESCRIPTIONS`' one-shot `UNKNOWN` + `WARNING` fallback is
the safety net for that, not new code to write.

`DPD-CH` (16th) was confirmed 2026-08-17 against the maintainer's own real,
separately-registered mydpd.ch account: it logs in and lists parcels through
the same shared backend as NL/UK, not the possibly-separate dedicated Swiss app
that earlier left it deliberately out. **Switzerland shares NL's infra;
Germany runs its own stack — do not conflate the two when reasoning about a
future country.** `DPD-CH` is a plain `DPD-<CC>` code, so it needs no override
entry (`ch` falls out of the default derivation, matching the mobileSlider
asset URL under `dpdgroup.com/ch/mydpd/`). Same live-capture caveat as the 14.

**`DPD-DE` is not in `BUSINESS_UNITS` — it runs its own build.** DE briefly
shipped in 2.8.0 as a blind pre-release, then was confirmed (2026-08-11, live
probe) to run on a wholly separate stack — `api.paketnavigator.de`, an ASP.NET
SOAP web service, no shared Keycloak realm — so it cannot work through `api.py`
at any BU value. The BU dropdown still carries a `DPD-DE` *option value*
(`_DE_BU_VALUE` in `config_flow.py`) purely to route the same form into the
separate path; it is never added to `BUSINESS_UNITS` or sent to `api.py`.

### DPD-UK rides on DPD-NL under the hood

`DPD-UK` is the 17th entry but is **not a real business-unit code**.
`carrier-research/dpd/dpd-uk.md` static-teardown'd the UK *mobile app* as its
own Firebase stack, separate from myDPD/Keycloak — that finding still stands.
But [`.github` discussion #14](https://github.com/ha-parcel-integrations/.github/discussions/14)
(2026-08-16/17) confirmed live that a UK account
logs in and lists its own parcels through the plain NL *web* myDPD flow (it
needed a password reset first, the account being SSO-only with no password).
The UK-GB/DPD-UK business unit itself exists nowhere on the shared backend —
absent from both the preferences dropdown and the myDPD app's own BU list
(`dpd-log.md`, 2026-08-11).

So **`BU_API_OVERRIDES` remaps `DPD-UK` → `DPD-NL` for every wire call**
(Keycloak, consignee-sso, parcels, detail) in `api.py`, via a `self._request_bu`
distinct from `self._bu`. `client.bu` keeps the configured `DPD-UK` for
`unique_id` and the tracking-URL fallback.

### DPD-UK's tracking URL is resolved live, not derived

DPD UK's real self-service tracker, `track.dpd.co.uk`, lives on a *third*
separate host again (`apis.track.dpd.co.uk`) from both myDPD and the app — but
its `GET /v1/reference?referenceNumber=<parcelNumber>&postcode=&origin=PRTK`
is **keyless**: confirmed live (2026-08-17) with no session cookie and an
empty/wrong postcode, all returning the same result — the postcode is not
checked at that step at all. It returns a DPD-assigned `data[0].parcelCode`
(`<14-digit-number>*<sequence>`, the sequence *not* a postcode formula) that
`track.dpd.co.uk/parcels/<parcelCode>` expects. Only the *next* step, `/login`,
needs reCAPTCHA — `ha-dpd` never calls it.

`DpdApiClient.async_get_uk_tracking_code` makes that one call;
`DpdCoordinator._enrich_uk_tracking_cache` caches the result per barcode for
the integration's lifetime (mirrors `_detail_cache`: never refetched, and a
failure is not retried either — the code cannot change once assigned).
`normalize_parcel(..., uk_tracking_code=...)` uses it when present.
`BU_COUNTRY_OVERRIDES["DPD-UK"] = "nl"` is the fallback for a barcode that
has not resolved yet or failed, not the primary link.

### Country and tracking-URL overrides

`_tracking_url` derives its country segment from the account's `bu` (`DPD-DE`
→ `/de/`) rather than hardcoding `/nl/`. **`BU_COUNTRY_OVERRIDES`** handles the
BU whose code does not map to its country the obvious way (`CHR-PT` → `pt`).
The same applies to `api.py`'s parcel-detail `businessUnit` param, which was
previously double chevron-prefixed as `DPD-DPD-NL` — unnoticed because
detail-call failures are swallowed.

**`BU_TRACKING_URL_OVERRIDES`** handles BUs whose tracking page is not
`dpdgroup.com/<country>/mydpd/...` at all — confirmed 2026-08-13 by a live link
check for `BRT` (Italy): the acquired BRT brand lives entirely on `mybrt.it`,
and `dpdgroup.com/it/mybrt/...` 404s, so this needed a full URL override, not a
brand-segment tweak. Check any newly-added acquired-brand BU (anything that is
not a plain `DPD-<CC>` code) against a real tracking link before assuming the
default template works.

### BU selector values are lower-case on purpose

The selector's option values are lower-case (`dpd-nl`, not `DPD-NL`) because
they double as translation keys and hassfest requires `[a-z0-9-_]+` with no
upper-case — the same rule that bit `ha-gls`'s country selector.
`async_step_user` immediately `.upper()`s the submitted value before use or
storage; the stored and internal `bu` value everywhere else (API calls,
`unique_id`, `entry.data`) stays upper-case. **Do not "simplify" this to a
single shared case** — the upper-case form is what DPD's API expects.

Every supported country's language has its own `translations/<lang>.json`,
including a translated `selector.bu.options` block for the dropdown itself —
not an English/Dutch/German UI with translated labels tacked onto foreign BUs.
The user step's `description` links a pre-filled "Add country" issue for
anything beyond the supported list.

### Germany: a separate SOAP transport

`countries/de/session.py` owns the SOAP session (envelope, signing, login
lifecycle); `countries/de/__init__.py` owns status derivation and
`normalize_parcel_de`.

**Two-stage login, documented nowhere DPD publishes** — discovered by capturing
a working client against a real account, not from the decompiled app the first
notes were based on. An anonymous `getSessionFullState` (empty `SessionToken`)
bootstraps a throwaway `SessionToken`; `getUserLogin` exchanges that plus
credentials for the real account `SessionToken` + `cloudUserID`. `async_login()`
always runs both steps — there is no one-stage path.

**The SOAP envelope double-wraps every call**: a bare `<methodName>` element
around an inner `<methodNameRequest>` element holding the fields
(`_build_envelope`); responses unwrap the same way
(`<methodNameResponse><methodNameResult>…`, case-sensitive, lowercase-first).
Getting either wrapper wrong does not 400 — the server NullReferenceExceptions
into a generic HTTP 500 SOAP fault, which `_async_raw_call` detects via
`"faultstring" in body` and raises `DpdApiError`, **not** `DpdAuthError`: a
fault is a shape bug, not a rejected login, and must not push a user into
reauth. `KeyPhase` is signed the same way (`compute_key_phase`; minute-derived
MD5, see its docstring) but was never the actual bug.

**Failure detection reads `Ack` plus a top-level singular `ErrorCode`**, not
the decompiled `ErrorDataList[]` shape alone — real rejections use both, and
`_error_codes()` unions them. `async_get_parcels()` / `async_call()` reauth
**once** on `ERROR_SESSION_NOT_VALID` / `ERROR_KEYPHASE`, never loop; two
consecutive `ERROR_KEYPHASE` responses warn once
(`_warn_keyphase_rotation_once`), since that likely means DPD rotated the
partner secret rather than routine expiry.

**Status is derivation-first, not enum-first** — `StatusID` has no closed
vocabulary, so `map_parcel_status_de` falls through `ParcelFlowTypeID` →
`DeliveryParcelShop.ParcelStatus` → `LastStatusInfo.StatusID` →
`StatusInfoContainer` slots (deepest-reached-first) → `isDelayed`/`showWarning`
→ `UNKNOWN`, with a one-shot warning at the first unmapped point. **As of
2026-08-17 only login and an empty inbox are wire-confirmed on a real
account** — the status vocabulary itself is unexercised; treat every mapped
`StatusID`/slot as provisional until a real parcel confirms it.

`CONF_DE_HARDWARE_ID` is persisted in `entry.data`, generated once at
config-flow time (`uuid4()`) and reused on every restart (`__init__.py` falls
back to a fresh one only if it is somehow missing) — a new id every setup would
look like a different device to DPD on every reload.

**No BU, no FMP, no per-parcel detail endpoint, no UK-style tracking lookup.**
`async_get_all_parcels_de` does all enrichment (including opt-in history via
`getTrackingScanList`) before `_async_fetch_de` ever sees the parcels, so DE's
coordinator path filters an already-normalized list
(`_apply_delivered_filter_canonical` in `parcels.py`, reading `delivered_at`
off the normalized shape, not `_apply_delivered_filter`'s raw-payload shape).

Debug logging in `_async_fetch_de` (`_LOGGER.debug`, gated behind
`isEnabledFor(DEBUG)`) mirrors the general path's shipment-count and
raw-payload summary — added after the initial build shipped with none, which
left no way to confirm a successful DE poll had fetched anything.

### Poland: a separate OAuth transport

`countries/pl/session.py` owns a public-client OAuth session against
`dpdsso.dpd.com.pl`: phone plus SMS enrolment via `async_send_sms` /
`async_register`, no client secret (a bogus code returns `invalid_grant`). The
returned refresh token is persisted on the config entry and rotated via
`token_updater` whenever a refresh response includes a new one.
`countries/pl/__init__.py` owns `map_parcel_status_pl` (derivation-first, no
closed vocabulary confirmed) and `normalize_parcel_pl`.

`_async_fetch_pl` fetches the receiver inbox in one call, then enriches only
the *active* parcels with a per-parcel detail call, bounded to 3 concurrent; a
failed detail fetch falls back to the list-only record rather than dropping the
parcel. Delivered and returned parcels are never detail-fetched.

**Unlike DE and general, PL has no outgoing shipments** — the surface is a
read-only receiver inbox, so `_async_fetch_pl` always returns an empty outgoing
list.

**PL is not capture-confirmed.** As of 2026-08-31
`carrier-research/dpd/dpd-pl.md` still carries `blocker: capture`: the status
vocabulary and payload shapes are sourced from an independent OSS
implementation, not this suite's own consented list/detail poll. Treat them as
provisional, and **ship PL as a pre-release (`bN`), not a normal minor bump,
until that capture happens.**

### Capabilities are per variant, not flat

`const.py` carries `CAPABILITIES_BY_VARIANT` with three keys — `Germany`,
`Poland`, `Other` — rather than a single flat `CAPABILITIES`, because the three
transports genuinely populate different fields:

| Variant | Never populated | Notes |
|---|---|---|
| `Other` | — | the full set |
| `Germany` | `url` | DE exposes no tracking-page link; does populate weight, dimensions, delivery_window, pickup_point |
| `Poland` | `url`, `weight`, `dimensions`, `pickup_point` | inbox payload carries none; does populate delivery_window via `planned_from` |

This replaced the single flat `CAPABILITIES` on 2026-08-23, which used to
overclaim `url` for DE. It feeds the comparison table on the docs site, so
**keep it in lockstep with any change to any of the three normalize
functions** — a wrong entry here is a wrong claim on the website.

### Status and pickup point

The raw description lives on `raw_status`, never `status`; unmapped falls to
`ParcelStatus.UNKNOWN` plus a one-shot WARNING (`_unknown_descriptions_logged`
/ `_unknown_event_types_logged`). `KNOWN_DESCRIPTIONS` and `_DESCRIPTION_MAP`
both need updating on a new DPD lifecycle stage.

**ParcelShop sensors**: `DpdEnRouteToParcelShopSensor` counts `pickup` parcels
with `status != at_pickup_point`; `DpdAwaitingPickupSensor` counts
`status == at_pickup_point`. Confirmed against a real DPD-CZ AlzaBox parcel
(2026-08-20, maintainer-supplied diagnostics) — both counted and transitioned
correctly end to end (`in_transit` → `at_pickup_point` → `delivered`).

**`pickup_point` is populated by repurposing the detail call's
`receiver.name`.** Confirmed live in that same AlzaBox capture: for a
`PARCELSHOP` delivery, DPD's per-parcel detail endpoint puts the ParcelShop's
own name (branch plus town) in `receiver.name` instead of a person — there is
no separate field carrying the actual recipient in that case. `normalize_parcel`
in `countries/general/__init__.py` reads that as `pickup_point` when `is_pickup`
and leaves `receiver` as `None` rather than mislabel a shop name as the
recipient; for a non-pickup delivery `receiver` is unaffected.
`normalize_parcel_de` mirrors the same display-name shape via `_address_name()`
on `DeliveryParcelShop.ParcelShop` — **not yet wire-confirmed on a real PUDO
delivery**, unlike the general path.

### Detail cache and FMP (cost control)

`_detail_cache` (barcode-keyed, integration-lifetime) lazily fills `receiver`,
`weight` and `dimensions` — at most one detail call per parcel. A **failed**
call is cached rather than retried every poll, and is retried once the parcel's
status moves: one hiccup must not mean missing data until restart.

The FMP delivery-window fetch is best-effort — any failure yields `None` and
the poll continues. `planned_from` / `planned_to` reflect the FMP hour window
when present, else the calendar-day window in the parcel's local timezone.

### Dynamic polling is unconditional

There is no user-facing polling interval — a deliberate suite-wide choice, not
a gap. It shipped as an opt-in `"auto"` dropdown value in 2.12.0, then
converged to unconditional: the `refresh_interval` option is gone entirely,
and an entry that still carries a stale value in its stored options is simply
never read for one.

The coordinator's initial interval is merely a starting point — the hot
cadence, so the first poll after setup happens promptly — and
`_async_update_data` recomputes it every refresh via `_next_update_interval`,
at the one shared point past the transport dispatch, so all three transports
get the same cadence logic:

- **Quiet window** (`QUIET_WINDOW_START_HOUR` 0 → `QUIET_WINDOW_END_HOUR` 6):
  no polling between those local hours except two daily anchors (00:00 and
  06:00). A computed next-due time that would land inside the window is
  clamped forward to the next anchor.
- **Hot tier** (`HOT_INTERVAL_MINUTES` 15) whenever any active incoming *or*
  outgoing parcel is `out_for_delivery`, from `HOT_LOOKAHEAD_HOURS` (1h)
  before its `planned_from` — or immediately when `planned_from` is missing or
  unparseable.
- **Mid tier** (`MID_INTERVAL_MINUTES` 45) otherwise. `problem` / `returning`
  deliberately stay here, not hot.
- **It never stops.** This is the account-based model: the mid-tier poll is
  also the only way to discover a shipment that appeared on the account
  without going through Home Assistant, so `update_interval` is never set to
  `None` even with nothing in flight.
- A deterministic per-`entry_id` stagger (`STAGGER_MINUTES` 7, hashed) is
  added to every computed interval so installs don't all hit a tier boundary
  or an anchor in the same second.

Diagnostics surfaces the result under `"polling"` (`current_tier_minutes`,
`update_interval_seconds`); `current_tier_minutes` is `None` until the first
successful refresh.

### History is opt-in, default OFF

`CONF_INCLUDE_HISTORY` adds **no new endpoint** — it reuses the detail call.
With the option on, the cache stores the status and refetches detail when a
barcode's status moves, so history grows on a status change; with it off the
cache is never refetched. **Do not collapse this back into "fetch once,
forever".** History reuses the parcel maps: only the consumer-realistic subset
of DPD's event codes is mapped (see `carrier-research/dpd/api/parcels.md`).

### Outgoing shipments and events

DPD splits server-side into `incomingShipments` / `sendingShipments`, so a
return the account ships back lands in `sendingShipments` and flows into the
outgoing sensors automatically — **no `isReturn` filtering needed here**,
unlike DHL. `_async_update_data` splits `sendingShipments` into active plus
delivered via the shared `_apply_delivered_filter`, feeding
`DpdOutgoingDeliveredParcelsSensor`.

Incoming events run over active plus delivered combined: the terminal hop fires
only `_delivered`, and `delivery_time_changed` only on a non-null `planned_*`
that differs. Outgoing events run over `outgoing_active` plus
`outgoing_delivered`; `delivered` wins the terminal hop; there is **no**
outgoing `registered` or `delivery_time_changed`. State lives in
`_known_state` / `_known_delivery_times` / `_known_outgoing_state`, and
`device_id` (from `_cached_device_id`) is on every payload.

### Entities and surfaces

`has_entity_name = True` plus `translation_key`, `icons.json`, translated
units — every summary sensor uses the single `parcels` unit. Device name is
`"DPD (<email>)"`; `_attr_attribution` is set; `_unrecorded_attributes` keeps
parcel lists and `history` out of the recorder.

**Per-parcel sensors are removed by the summary sensor** — the old self-remove
raced with listener cleanup and left ghosts. **Setup cleanup is sensor-scoped**
(filter `domain == "sensor"`, else it deletes the button); every non-parcel
`{entry_id}_*` sensor **must** stay in `non_parcel_unique_ids`.

Also shipped: a refresh `button`, a diagnostic `last_update` sensor
(`coordinator.last_success_time`), and a deliveries `calendar` — read-only over
`incoming_active`, no extra API calls, enabled by default.

## Adding a business unit

1. Add the entry to `BUSINESS_UNITS` in `const.py` (upper-case `value`).
2. Add its lower-case option to **every** `translations/<lang>.json`'s
   `selector.bu.options` — not just `en.json`. Verify with a structural
   key-parity check (compare each file's flattened key set against `en.json`)
   before committing; a bad interleave has slipped through once already.
3. If the code is not a plain `DPD-<CC>`, check its real tracking link and add
   `BU_COUNTRY_OVERRIDES` / `BU_TRACKING_URL_OVERRIDES` entries as needed.
4. Leave `CAPABILITIES_BY_VARIANT["Other"]` alone unless the BU genuinely
   populates a different field set.

## Adding a country transport

1. Create `countries/<cc>/` with a `session.py` (transport) and an
   `__init__.py` (status mapper + `normalize_parcel_<cc>`).
2. Construct the session in `__init__.py` based on `CONF_COUNTRY` and pass it
   to `DpdCoordinator`.
3. Add one branch at the single dispatch point in `_async_update_data` and a
   matching `_async_fetch_<cc>`. Change nothing downstream of it.
4. Add a `CAPABILITIES_BY_VARIANT` key describing exactly which canonical
   fields the new normalizer populates.
5. Add its language to `translations/`, and route the config flow to it.
