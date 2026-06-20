"""Constants for the DPD integration."""
from homeassistant.const import Platform

DOMAIN = "dpd"

PLATFORMS: list[Platform] = [Platform.SENSOR]

POLL_INTERVAL = 900  # seconds (15 minutes)

KEYCLOAK_TOKEN_URL = (
    "https://login.dpdgroup.com/auth/realms/login/protocol/openid-connect/token"
)
KEYCLOAK_CLIENT_ID = "MOBILE-APP-PROD"

DPD_BASE_URL = "https://www.dpdgroup.com/concept/webservice"
DPD_GUEST_TOKEN_URL = f"{DPD_BASE_URL}/oauth/token"
DPD_CONSIGNEE_SSO_URL = f"{DPD_BASE_URL}/users/login/consignee-sso"
DPD_PARCELS_URL = f"{DPD_BASE_URL}/v7/parcels"

# Follow My Parcel — DPD's same-day delivery-window sub-API. Per-parcel
# auth flow: take the hashcode from
# ``availableActions.FOLLOW_MY_PARCEL[0].hashcode`` on the shipment,
# exchange it for an FMP access token at the authenticate endpoint, then
# fetch the shipment detail to read ``deliveryDateAndTime.timeRange``
# (``from`` / ``to``).
DPD_FMP_AUTHENTICATE_URL = f"{DPD_BASE_URL}/fmp/authenticate"
DPD_FMP_SHIPMENT_URL = f"{DPD_BASE_URL}/v3/fmp/shipment"

# myDPD Mobile App client credentials (base64 of "<client_id>:<client_secret>"),
# fetched from DPD's Firebase Remote Config and hardcoded in the mobile app.
DPD_BASIC_TOKEN = (
    "bXlEUEQgTW9iaWxlIEFwcDpaMVdzeTQ4RGpseWcweDdVWjhvWTlYdmZIT2xIbW4yTmpJdnYycmpVVjY3N1hDOGhiTGlkNHY2OWpCQzlvZnpU"
)

USER_AGENT = "okhttp/4.12.0"

CONF_BU = "bu"

BUSINESS_UNITS = [
    {"value": "DPD-NL", "label": "Netherlands"},
]

DEFAULT_BU = "DPD-NL"

# Known DPD `status.description` strings, in roughly the order a parcel
# moves through. The numeric `status.status` follows the same 0 → 5
# progression in samples we have seen.
#
# These constants are descriptive — the only filter we apply today is
# "delivered vs everything-else" (using `DELIVERED_DESCRIPTION`). The
# 2.0.0 normalization layer will map each value onto the canonical
# `ParcelStatus` enum used by the other carriers.
STATUS_ORDER_CREATED = "ORDER_CREATED"                  # 0 — label printed; not yet collected
STATUS_PARCEL_HANDED = "PARCEL_HANDED"                  # 1 — handed to DPD by the sender
STATUS_IN_TRANSIT = "IN_TRANSIT"                        # 2 — in DPD's network
STATUS_AT_DELIVERY_CENTER = "AT_DELIVERY_CENTER"        # 3 — at the regional sorting hub the morning of delivery
STATUS_PARCEL_OUT_FOR_DELIVERY = "PARCEL_OUT_FOR_DELIVERY"  # 4 — on the delivery vehicle today
STATUS_DELIVERED = "DELIVERED"                          # 5 — terminal

# Terminal status — every other status.description is treated as "active".
DELIVERED_DESCRIPTION = STATUS_DELIVERED

# All description values the integration recognises. Anything outside this
# set is still treated as "active" (so we never accidentally swallow a
# parcel) and surfaced via a one-shot info log so we can grow the list.
KNOWN_DESCRIPTIONS: frozenset[str] = frozenset({
    STATUS_ORDER_CREATED,
    STATUS_PARCEL_HANDED,
    STATUS_IN_TRANSIT,
    STATUS_AT_DELIVERY_CENTER,
    STATUS_PARCEL_OUT_FOR_DELIVERY,
    STATUS_DELIVERED,
})

CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7
