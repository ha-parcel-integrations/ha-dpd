"""Sample DPD API payloads shared by the test modules.

The shape of a ``my-parcels`` response, trimmed to the fields the integration
reads. Keep them here rather than inline in each test module: when the payload
shape turns out to differ from what we assumed, there is then exactly one place
to fix.

Every helper returns a **fresh** dict, so a test may mutate what it gets back
without leaking into the next one.
"""
from __future__ import annotations

from typing import Iterable

DELIVERED_NO = "01XXXXXXXXXXXX"


def shipment_sample(
    description: str = "DELIVERED",
    parcel_number: str = DELIVERED_NO,
    event_dt: str | None = None,
    tz_id: str | None = "Europe/Amsterdam",
    delivery_date: str | None = None,
    fmp_hashcode: str | None = None,
    fmp_window: dict | None = None,
    delivery_time_from: str | None = None,
    delivery_time_to: str | None = None,
) -> dict:
    """One entry of ``incomingShipments`` / ``sendingShipments``.

    Optional keys are omitted rather than set to ``None``: DPD leaves them out
    entirely, and several code paths distinguish absent from null.
    """
    status: dict = {"description": description}
    if event_dt is not None:
        status["eventDateAndTime"] = event_dt
    if tz_id is not None:
        status["eventDateAndTimeZoneId"] = tz_id
    out: dict = {"parcelNumber": parcel_number, "status": status}
    if delivery_date is not None:
        out["deliveryDate"] = delivery_date
    if fmp_hashcode is not None:
        out["availableActions"] = {"FOLLOW_MY_PARCEL": [{"hashcode": fmp_hashcode}]}
    if fmp_window is not None:
        out["fmpDeliveryDateAndTime"] = fmp_window
    if delivery_time_from is not None:
        out["deliveryTimeFrom"] = delivery_time_from
    if delivery_time_to is not None:
        out["deliveryTimeTo"] = delivery_time_to
    return out


def envelope(
    incoming: Iterable[dict] = (),
    sending: Iterable[dict] = (),
) -> dict:
    """The two-list envelope ``async_get_parcels`` returns."""
    return {
        "incomingShipments": list(incoming),
        "sendingShipments": list(sending),
    }
