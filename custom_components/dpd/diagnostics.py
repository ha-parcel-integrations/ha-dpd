"""Diagnostics support for the DPD integration."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import DpdConfigEntry
from .const import CONF_DE_HARDWARE_ID, CONF_PHONE, CONF_REFRESH_TOKEN, CONF_SMS_CODE

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_DE_HARDWARE_ID,
    CONF_PHONE,
    CONF_SMS_CODE,
    CONF_REFRESH_TOKEN,
    "email",
    "parcelNumber",
    "senderName",
    "recipientName",
    "postalCode",
    "street",
    "houseNumber",
    "city",
    "phoneNumber",
    "courier_phone",
    "latitude",
    "longitude",
    "waybill",
    "access_token",
    "refresh_token",
    # DPD Polska's full carrier response is intentionally exposed in ``raw``.
    # Diagnostics must still be safe to attach to an issue.
    "sender",
    "receiver",
    "address",
    "phone",
    "coordinates",
    "validation_token",
    "mps",
    "pickup_pin",
    "pick_up_pin",
    "zabka_barcode",
    # DPD Germany field names (different casing/shape from the general
    # backend above) — the session token and every address/contact field
    # an AddressType can carry.
    "SessionToken",
    "HardwareID",
    "ParcelNo",
    "Street",
    "HouseNo",
    "ZipCode",
    "City",
    "Phone",
    "Mail",
    "FirstName",
    "LastName",
    "Name",
    "Company",
    "ReceiverName",
    # DPD Austria (mydpd.at) field names. Its spellings match none of the
    # entries above — ``parcelno`` is not ``parcelNumber``/``ParcelNo``, and
    # the address block is ``*_addr_*``, not ``postalCode``/``ZipCode`` — so
    # every one has to be listed. Taken from a confirmed payload, not from
    # the reconstructed names: an earlier guess at ``plz``/``city`` matched
    # nothing and left the whole address block in clear text.
    "parcelno",
    "parcel_name",
    "gkz",
    "cusr_id",
    "cincoming_id",
    "verifiedPlz",
    "statusMessage",
    # Either party's name, company and address. Both directions matter: the
    # inbox carries sent parcels too.
    "consignee_name1",
    "consignee_company_name",
    "consignee_addr_country",
    "consignee_addr_postcode",
    "consignee_addr_city",
    "consignee_addr_street",
    "sender_name1",
    "sender_company_name",
    "sender_addr_country",
    "sender_addr_postcode",
    "sender_addr_city",
    "sender_addr_street",
    "sender_cust_no",
    # Delivery and last-scan coordinates, and the typed detail payload on a
    # scan entry — which carries a neighbour's or the recipient's name for
    # ``pers``/``pers2``/``aviso``, and a ParcelShop's address for ``shop``.
    "dstCoords",
    "lstCoords",
    "infoData",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DpdConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a DPD config entry."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data or {}

    return {
        "entry_data": async_redact_data(dict(entry.data), TO_REDACT),
        "entry_options": dict(entry.options),
        "last_update_success": coordinator.last_update_success,
        "counts": {
            "incoming_active": len(data.get("incoming_active", [])),
            "incoming_delivered": len(data.get("incoming_delivered", [])),
            "outgoing_active": len(data.get("outgoing_active", [])),
            "outgoing_delivered": len(data.get("outgoing_delivered", [])),
        },
        "polling": {
            "current_tier_minutes": coordinator.current_tier_minutes,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "incoming_active": async_redact_data(data.get("incoming_active", []), TO_REDACT),
        "incoming_delivered": async_redact_data(
            data.get("incoming_delivered", []), TO_REDACT
        ),
        "outgoing_active": async_redact_data(data.get("outgoing_active", []), TO_REDACT),
        "outgoing_delivered": async_redact_data(
            data.get("outgoing_delivered", []), TO_REDACT
        ),
    }
