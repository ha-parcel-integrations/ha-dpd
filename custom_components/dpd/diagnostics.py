"""Diagnostics support for the DPD integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED, async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import DpdConfigEntry
from .const import (
    CONF_COUNTRY,
    CONF_DE_HARDWARE_ID,
    CONF_DEVICE_ID,
    CONF_MAGIC_LINK,
    CONF_PHONE,
    CONF_REFRESH_TOKEN,
    CONF_SMS_CODE,
    COUNTRY_UK,
)

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    CONF_DE_HARDWARE_ID,
    CONF_PHONE,
    CONF_SMS_CODE,
    CONF_REFRESH_TOKEN,
    CONF_DEVICE_ID,
    CONF_MAGIC_LINK,
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
    # DPD UK (consumer app): Firebase and session material, the account's
    # identifiers and the tracking code embedded in the tracking url.
    "refreshToken",
    "idToken",
    "customToken",
    "dpdSession",
    "consumerId",
    "phone_number",
    "deviceId",
    "parcelCode",
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
    # backend above). The first group belongs to entries and diagnostics
    # captured before the web account replaced the app transport.
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
    # DPD Germany's parcel label is a name the user typed.
    "label",
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


# The fields of a UK record that say how DPD reports a parcel and nothing about
# who it is for. An allowlist, because a field that has only ever been null
# cannot be judged by its name, and the record carries the recipient.
_UK_RAW_ALLOWED = (
    "businessUnit",
    "canCollectParcel",
    "collectedFromShop",
    "collectionDate",
    "customerTargetDate",
    "deliveredToConsumer",
    "deliveryAttempts",
    "deliveryBusinessUnit",
    "deliveryDetails.numberOfParcels",
    "deliveryDetails.podDetails.podDate",
    "deliveryDetails.podDetails.podSafePlaceCode",
    "deliveryDetails.totalWeight",
    "deliveryDetails.verifiedWeight",
    "depotTargetDate",
    "estimatedDeliveryActionCode",
    "estimatedDeliveryDate",
    "estimatedDeliveryEndTime",
    "estimatedDeliveryStartTime",
    "etaEtaTime",
    "invoiceDate",
    "issueDetails.issueCode",
    "issueDetails.issueDelayDays",
    "ivrStatusCode",
    "labelSentMethod",
    "lastConfirmDate",
    "lastInstructionDate",
    "messageActionCode",
    "messageResponseCode",
    "nextSteps.code",
    "outForDeliveryDetails.mapAvailable",
    "outForDeliveryDetails.outForDeliveryDate",
    "outOfHomeType",
    "parcelStatusType",
    "parcelTypeName",
    "pickupAvailable",
    "pickupStatus",
    "pickupType",
    "product.productCode",
    "product.productDescription",
    "service.serviceCode",
    "service.serviceDescription",
    "serviceEndTime",
    "shipmentDate",
    "transferToAgent",
    "undelivered",
)


def _uk_safe_raw(raw: Any) -> dict[str, Any]:
    """Copy only the allowlisted fields of a UK record."""
    safe: dict[str, Any] = {}
    for path in _UK_RAW_ALLOWED:
        parts = path.split(".")
        node = raw
        for part in parts:
            if not isinstance(node, dict) or part not in node:
                break
            node = node[part]
        else:
            # An allowlisted name holding an object is no longer the field
            # that was judged.
            if not isinstance(node, dict):
                target = safe
                for part in parts[:-1]:
                    target = target.setdefault(part, {})
                target[parts[-1]] = node
    return safe


def _uk_parcel(parcel: dict[str, Any]) -> dict[str, Any]:
    """Make one UK parcel safe; its status sentence can name a place or person."""
    return {
        **parcel,
        "barcode": REDACTED,
        "url": REDACTED,
        "raw_status": REDACTED,
        "raw": _uk_safe_raw(parcel.get("raw")),
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: DpdConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a DPD config entry."""
    coordinator = entry.runtime_data.coordinator
    data = coordinator.data or {}
    if entry.data.get(CONF_COUNTRY) == COUNTRY_UK.upper():
        data = {
            key: [_uk_parcel(parcel) for parcel in parcels]
            for key, parcels in data.items()
        }

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
        "incoming_active": async_redact_data(
            data.get("incoming_active", []), TO_REDACT
        ),
        "incoming_delivered": async_redact_data(
            data.get("incoming_delivered", []), TO_REDACT
        ),
        "outgoing_active": async_redact_data(
            data.get("outgoing_active", []), TO_REDACT
        ),
        "outgoing_delivered": async_redact_data(
            data.get("outgoing_delivered", []), TO_REDACT
        ),
    }
