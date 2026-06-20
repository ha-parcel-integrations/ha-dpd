# DPD API reference

The DPD endpoints used by this integration are reverse-engineered from the **myDPD mobile app** and are not officially documented. They may change without notice.

| Endpoint | Description |
|----------|-------------|
| [auth.md](auth.md) | Three-step authentication: Keycloak login → mobile-app guest token → consignee SSO exchange |
| [parcels.md](parcels.md) | Polled endpoint that returns the user's incoming and outgoing shipments |
| [fmp.md](fmp.md) | Follow My Parcel — per-parcel sub-API for the precise delivery-window (`from` / `to` time range) on the day of delivery |

## Common conventions

- **Base URL:** `https://www.dpdgroup.com/concept/webservice`
- **Auth realm:** `https://login.dpdgroup.com/auth/realms/login`
- **User-Agent:** `okhttp/4.12.0` (matches the mobile app)
- **Business Unit (`bu`):** identifies the country (`DPD-NL` for the Netherlands, others TBD)
