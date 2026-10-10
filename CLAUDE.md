# Working in this repository

Home Assistant custom integration for DPD parcel tracking. Distributed via HACS;
not part of HA core. **Silver** quality tier, minimum HA `2024.12.0`. No DTO layer.

Three places hold the knowledge, and they do not overlap:

| What | Where |
|---|---|
| How this integration is built, and why it is built that way | [`ARCHITECTURE.md`](ARCHITECTURE.md) — read it before touching the coordinator's dispatch, a `countries/` package, or the business-unit tables |
| Endpoint mechanics, auth flows, status vocabularies | `carrier-research/dpd/api/` (private repo). **Never** duplicated into this repo |
| Suite-wide conventions | [`.github/CONVENTIONS.md`](https://github.com/ha-parcel-integrations/.github/blob/main/CONVENTIONS.md) |

This file is the short list of things an agent must not get wrong.

## Shared conventions — fetch when relevant

Don't fetch `CONVENTIONS.md` every session — fetch it **before** you act in one
of these areas:

| Before you … | Fetch `CONVENTIONS.md` § |
|---|---|
| touch entities, sensors, config/options flow, coordinator, diagnostics, translations | *Home Assistant developer docs* (its table points on to the canonical HA page — don't rely on memory) |
| add/rename a parcel field, a `ParcelStatus`, or a bus event; change first-refresh or unmapped-status logging | *Parcel contract* (this repo implements it; below is only where DPD deviates) |
| consider "fixing" a lint/pattern the skill flags (inline client) | *Deliberate skill divergences* — likely intentional, don't re-flag |
| commit, bump, tag, release, or write release notes; add a feature without a test | *Workflow / Commits / Versioning / Testing* |

**Suite-wide tripwire, kept inline on purpose:** the first refresh runs in
`__init__.py` *before* `async_forward_entry_setups`, never in a platform — from a
forwarded platform HA can't catch `ConfigEntryNotReady` and half-sets-up the
entry. Runtime-only; the tests don't catch a regression here.

## Load-bearing DPD decisions — do not refactor away

**Auth & setup**
- **Auth-tier 5xx → `ConfigEntryNotReady`**: when Keycloak returns a non-JSON 5xx
  page, `api.py` raises `DpdApiError(status_code)` before parsing; `__init__.py`
  maps it to `ConfigEntryNotReady` (retry with backoff) instead of crashing on a
  `JSONDecodeError` or forcing reauth.
- **Reauth** uses `async_update_reload_and_abort`; the confirm step guards with
  `async_set_unique_id` + `_abort_if_unique_id_mismatch` so a *different* account's
  credentials abort instead of rebinding.
- **Options flow** has no `entry.add_update_listener` — `async_schedule_reload` on
  submit. Two sections left, `delivered` and `history`; polling is not among
  them (see below).
- `aiohttp.ClientError` is not caught in the coordinator (wrapped automatically).
  Config: `ConfigEntry.runtime_data` (`DpdData`), `PARALLEL_UPDATES = 0`,
  coordinator takes `config_entry=entry`.

**Polling cadence is not configurable — don't add the option back.** The
account-based dynamic-polling algorithm always runs: the coordinator
recomputes `update_interval` at the end of every
`_async_update_data`, at the single shared point past the transport dispatch,
so all four transports get the same cadence. A 15 min hot tier the moment any
active incoming *or* outgoing parcel is `out_for_delivery` (starting 1h before
`planned_from`, or immediately if missing), a 45 min mid tier otherwise — which
never stops, since the account call is the only way to discover a new shipment
that appears without going through this integration — and a 00:00–06:00
local-time quiet window with anchor polls at each end, plus a small
deterministic per-`entry_id` stagger. `problem`/`returning` stay in the mid
tier, not hot. Surfaced in diagnostics under `"polling"`
(`current_tier_minutes`, `update_interval_seconds`). The `refresh_interval`
dropdown (Phase 1, shipped 2.12.0) is gone; a stale stored value is never read.
Full model: [`ARCHITECTURE.md`](ARCHITECTURE.md).

**Four transports, one dispatch point** — `DpdCoordinator._async_update_data`
branches on which session `__init__.py` constructed (`_de_session` →
`_async_fetch_de`, `_pl_session` → `_async_fetch_pl`, `_at_session` →
`_async_fetch_at`, else the general/BU path through `api.py`). Everything past
that branch — sorting, filtering, event firing, the polling recompute — is
shared. **Never add a branch downstream of it.** Full model, per-transport detail and the evidence trail:
[`ARCHITECTURE.md`](ARCHITECTURE.md).

**Business unit** — `DPD-DE` and `DPD-AT` are deliberately **not** in
`BUSINESS_UNITS`; each runs its own stack, routed via `_DE_BU_VALUE` /
`_AT_BU_VALUE` in `config_flow.py`.
`DPD-UK` is in the list but rides on `DPD-NL` for every wire call via
`BU_API_OVERRIDES`. Two tripwires: BU selector option values are **lower-case**
(hassfest) and `.upper()`'d immediately in `config_flow.py` — don't "simplify"
that to one case, DPD's API expects upper — and a new BU needs an entry in
`const.py` **and** in *every* `translations/<lang>.json`'s
`selector.bu.options`, not just `en.json`.

**Germany (`countries/de/`)** — separate SOAP transport. A SOAP fault raises
`DpdApiError`, **never** `DpdAuthError`: a fault is a shape bug, not a rejected
login, and must not push a user into reauth. Reauth on
`ERROR_SESSION_NOT_VALID`/`ERROR_KEYPHASE` happens **once**, never in a loop.
As of 2026-08-17 only login and an empty inbox are wire-confirmed on a real
account — **treat every mapped status/slot as provisional.**

**Poland (`countries/pl/`)** — separate OAuth transport, receiver inbox only
(always returns an empty outgoing list). As of 2026-08-31
`carrier-research/dpd/dpd-pl.md` still carries `blocker: capture`: payload
shapes come from an independent OSS implementation, not our own consented poll.
**Ship PL as a pre-release (`bN`), not a normal minor bump, until that capture
happens.**

**Austria (`countries/at/`)** — the Austrian consumer portal, authenticated by
a bearer token from the user's own email/password login. Mechanics (endpoints,
request/response shapes, the status vocabulary, the scan-stamp format) are in
`carrier-research/dpd/`, not here. The tripwires:

- **The success flag is not at a fixed depth and the token rotates per
  response.** `_unwrap` owns both. Flattening it makes login work and every
  poll fail; moving token handling back into the login path expires the
  session. Both were live bugs on 2026-10-10.
- **Only a real credential rejection may reach reauth.** The host runs bot
  protection whose refusal looks like an authorization failure, and a rejection
  can arrive with a success status code. Mapping either to `DpdAuthError` sends
  a user with working credentials into reauth — the same mistake the DE
  SOAP-fault rule exists to prevent. A missing token on an accepted login is a
  shape problem, not an auth one.
- **The portal needs one page GET before the API is usable**, best-effort by
  design — don't make it fatal.
- **Never infer a wire format from the portal's renderer.** It slices only what
  it displays, which is how the scan-stamp format was got wrong; every
  timestamp was dropped and a delivered parcel reported no `delivered_at`. It
  failed as a missing field, not an error.
- **Two status vocabularies; prefer the stable one.** Each history entry
  carries a language-independent identifier beside the numeric stage, and it
  wins where it maps. `map_parcel_status_at` resolves terminal → failure →
  returns → pickup point → name → stage; reordering regresses real cases (a
  delivered parcel with an earlier failed attempt, a parcel at a ParcelShop
  whose stage still reads out-for-delivery). Status *text* is localised prose —
  `raw_status` only, never a key.
- **Trust the lifecycle, not the parcel-level `state` object.** Its own
  `delivered` reads false on a delivered parcel, and its delivery times are the
  account's standing preference, not a window. `verified: false` does **not**
  mean a parcel has no lifecycle.
- **The returns *tab* is not a return.** The carrier's own flags decide; the
  tab is only where the portal files a parcel. Falling back to the tab when the
  flags say false reported every parcel in it as `returning`.
- **Diagnostics redaction needs Austria's own spellings for every PII field**,
  including both parties' address blocks, the coordinates and a scan entry's
  typed detail payload. None matches an existing entry, and a guess at the
  names from reconstructed research left the whole address block in clear
  text. `tests/test_diagnostics.py` guards it.

As of 2026-10-10 the transport **and** one delivered parcel are wire-confirmed.
Still unseen: one stage value, an active parcel (so the delivery window is
unexercised), a pickup-point parcel, a failure and a return. **Ship AT as a
pre-release (`bN`)** until those land, same rule as PL.

**`CAPABILITIES_BY_VARIANT` (not a flat `CAPABILITIES`)** — four keys,
`Germany` / `Poland` / `Austria` / `Other`, because the transports populate
different fields. It feeds the docs-site comparison table, so a wrong entry is a wrong
claim on the website. **Keep it in lockstep with any change to any of the
`normalize_parcel*` functions.**

**Parcel core** — unmapped `raw_status` falls to `ParcelStatus.UNKNOWN` with a
one-shot WARNING (`KNOWN_DESCRIPTIONS` / `_DESCRIPTION_MAP` both need updating
on a new DPD lifecycle stage). Per-parcel sensors are removed **by the summary
sensor**, not individually — self-removal races and leaves ghosts. Setup
cleanup is scoped to `domain == "sensor"` (else it deletes the refresh button)
and every non-parcel `{entry_id}_*` sensor **must** be listed in
`non_parcel_unique_ids`.

## Planned / skipped

- **Planned (next major)**: exception translations (`UpdateFailed` f-strings →
  `translation_key` + placeholders).
- **Shipped (2026-08-20)**: `pickup_point` — see *Status and pickup point* in
  [`ARCHITECTURE.md`](ARCHITECTURE.md).

## Running tests

```
python -m pytest tests/ --cov=custom_components.dpd
```

Coverage must stay **above 95%** (silver `test-coverage` rule). Run before
committing. A code change updates the README, `ARCHITECTURE.md` and this file
in the same commit; API mechanics go to `carrier-research/dpd/`, never here.
