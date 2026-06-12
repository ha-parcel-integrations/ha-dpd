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

# Terminal status — every other status.description is treated as "active".
# Other values seen so far: ORDER_CREATED. More TBD once we observe in-transit parcels.
DELIVERED_DESCRIPTION = "DELIVERED"

CONF_DELIVERED_FILTER_TYPE = "delivered_filter_type"
CONF_DELIVERED_FILTER_AMOUNT = "delivered_filter_amount"
DEFAULT_DELIVERED_FILTER_TYPE = "days"
DEFAULT_DELIVERED_FILTER_AMOUNT = 7
