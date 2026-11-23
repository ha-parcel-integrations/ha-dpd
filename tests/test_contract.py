"""The parcel contract's checks, run over every country's normalised samples."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from custom_components.dpd.const import ParcelStatus
from custom_components.dpd.countries.at import normalize_parcel_at
from custom_components.dpd.countries.de import normalize_parcel_de
from custom_components.dpd.countries.general import normalize_parcel
from custom_components.dpd.countries.pl import normalize_parcel_pl
from custom_components.dpd.countries.uk import normalize_parcel_uk
from custom_components.dpd.parcels import is_pickup, planned_for_status, planned_window

from .payloads import shipment_sample

CANONICAL_KEYS = [
    "carrier",
    "barcode",
    "sender",
    "receiver",
    "contents",
    "status",
    "raw_status",
    "delivered",
    "delivered_at",
    "planned_from",
    "planned_to",
    "pickup",
    "pickup_point",
    "url",
    "weight",
    "dimensions",
    "history",
    "raw",
]

_AMS = ZoneInfo("Europe/Amsterdam")
_TODAY = date(2099, 10, 20)


def _general(description: str, **kwargs):
    return lambda: normalize_parcel(
        shipment_sample(description, event_dt="2099-10-20T10:00:00", **kwargs)
    )


def _pl(status: str, planned: str | None = "2099-09-10", *, shop: bool = False):
    def build():
        return normalize_parcel_pl(
            {
                "waybill": "12345678901234",
                "main_status": {"status": status},
                "statuses": [
                    {"status": "READY_TO_SEND", "date": "2099-09-08T10:00:00Z"},
                    *(
                        [{"status": "READY_TO_PICK_UP", "date": "2099-09-09T10:00:00Z"}]
                        if shop
                        else []
                    ),
                ],
                "delivery": {
                    "planned_delivery_date": planned,
                    "delivered_datetime": "2099-09-11T10:00:00Z",
                },
            },
            include_history=True,
        )

    return build


def _uk(**over):
    def build():
        return normalize_parcel_uk(
            {
                "parcelNumber": "15501234567891",
                "parcelCode": "15501234567891*002",
                "deliveredToConsumer": False,
                "undelivered": False,
                "collectedFromShop": False,
                "estimatedDeliveryDate": "2099-10-20",
                "estimatedDeliveryStartTime": "09:00",
                "estimatedDeliveryEndTime": "11:00",
                "lastConfirmDate": "2099-10-20 12:00:00",
                **over,
            },
            inactive=False,
        )

    return build


def _at(stage: int, entries: list[dict], direction: str = "inc", **lifecycle):
    def build():
        return normalize_parcel_at(
            {
                "parcelno": "01234567890123",
                "type": direction,
                "consignee_name1": "",
                "lifecycle": {
                    "state": stage,
                    "state_info": "info",
                    "entries": entries,
                    "predict": ["10:00", "12:00"],
                    "deliver_date": None,
                    **lifecycle,
                },
            },
            today=_TODAY,
        )

    return build


def _at_entry(name=None, info_type=None, info_data=None, stamp="20991019140500"):
    state: dict = {"text": "x"}
    if name:
        state["name"] = name
    if info_type:
        state["infoType"] = info_type
        state["infoData"] = info_data
    return {"datetime": stamp, "state": state}


_SHOP = _at_entry(info_type="shop", info_data={"name1": "Example Shop"})
_EARLY_SHOP = _at_entry(
    info_type="shop", info_data={"name1": "Drop-off Shop"}, stamp="20991018100000"
)
_SLOTS = ("Start", "OnTheRoad", "DeliveryDepot", "CarLoad", "Delivered")


def _de(sentence: str, icon: int, pairs: list[tuple[str, str]]):
    pairs = pairs + [("-", "")] * (5 - len(pairs))
    slots = [
        {"slot": name, "text": text, "date": day}
        for name, (text, day) in zip(_SLOTS, pairs, strict=True)
    ]
    return lambda: normalize_parcel_de(
        {
            "barcode": "01234567890123",
            "label": "x",
            "status_sentence": sentence,
            "status_icon": icon,
            "tab": "incoming",
            "detail": {
                "identity": "x",
                "slots": slots,
                "status_icon": icon,
                "weight_text": "",
                "flow_type": "0",
                "ucid": "",
            },
            "stale_detail": None,
        },
        include_history=True,
        today=date(2099, 10, 20),
    )


SAMPLES = {
    "general_active": _general("PARCEL_OUT_FOR_DELIVERY", delivery_date="2099-10-20"),
    "general_delivered": _general("DELIVERED"),
    "general_at_pickup": _general(
        "AVAILABLE_FOR_COLLECTION", delivery_date="2099-10-20"
    ),
    "general_returning": _general("RETURN_TO_SENDER", delivery_date="2099-10-20"),
    "pl_active": _pl("HANDED_OVER_FOR_DELIVERY"),
    "pl_moment": _pl("HANDED_OVER_FOR_DELIVERY", "2099-09-10T13:00:00"),
    "pl_at_pickup": _pl("READY_TO_PICK_UP"),
    "pl_collected": _pl("PICKED_UP", shop=True),
    "pl_returned": _pl("RETURNED_TO_SENDER"),
    "pl_expired": _pl("EXPIRED_PICKUP"),
    "pl_bare": lambda: normalize_parcel_pl({"waybill": "1"}),
    "uk_active": _uk(),
    "uk_at_pickup": _uk(parcelStatusType=5),
    "uk_collected": _uk(collectedFromShop=True, pickupLocationCode="S1"),
    "uk_delivered": _uk(deliveredToConsumer=True),
    "at_out_for_delivery": _at(4, [_at_entry()]),
    "at_at_pickup": _at(4, [_SHOP]),
    "at_collected": _at(5, [_SHOP, _at_entry("DELIVERED")], delivered=True),
    "de_active": _de(
        "Paket in Zustellung", 3, [("Paketinfo an DPD übergeben", "15.10.")]
    ),
    "de_delivered": _de(
        "Paket zugestellt",
        4,
        [
            ("Paketinfo an DPD übergeben", "15.10."),
            ("Paket unterwegs", "16.10."),
            ("Paket im Paketzustellzentrum", "17.10."),
            ("Paket in Zustellung", "18.10."),
            ("Paket zugestellt", "18.10."),
        ],
    ),
}


@pytest.fixture(params=SAMPLES)
def parcel(request) -> dict:
    return SAMPLES[request.param]()


def test_keys_and_order(parcel):
    assert list(parcel) == CANONICAL_KEYS


def test_the_carrier_fills_no_contents(parcel):
    assert parcel["contents"] is None


def test_delivered_follows_status(parcel):
    if parcel["status"] is ParcelStatus.DELIVERED:
        assert parcel["delivered"] is True
    elif parcel["status"] is not ParcelStatus.RETURNING:
        assert parcel["delivered"] is False


def test_delivered_at_only_when_delivered(parcel):
    if not parcel["delivered"]:
        assert parcel["delivered_at"] is None


def test_planned_window_is_ordered_and_anchored(parcel):
    if parcel["planned_to"] is not None:
        assert parcel["planned_from"] is not None
        assert datetime.fromisoformat(parcel["planned_to"]) > datetime.fromisoformat(
            parcel["planned_from"]
        )


def test_planned_is_empty_when_the_parcel_is_done_or_waiting(parcel):
    if parcel["status"] in (
        ParcelStatus.DELIVERED,
        ParcelStatus.AT_PICKUP_POINT,
        ParcelStatus.RETURNING,
    ):
        assert parcel["planned_from"] is None
        assert parcel["planned_to"] is None


def test_pickup_is_a_bool_and_the_point_needs_it(parcel):
    assert isinstance(parcel["pickup"], bool)
    if not parcel["pickup"]:
        assert parcel["pickup_point"] is None


@pytest.mark.parametrize("name", ["pl_collected", "uk_collected", "at_collected"])
def test_a_collected_pickup_parcel_stays_pickup(name):
    parcel = SAMPLES[name]()
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True


def test_pl_date_covers_the_local_day():
    parcel = SAMPLES["pl_active"]()
    assert parcel["planned_from"] == "2099-09-10T00:00:00+02:00"
    assert parcel["planned_to"] == "2099-09-10T23:59:59+02:00"


def test_pl_moment_ends_at_the_end_of_the_day():
    parcel = SAMPLES["pl_moment"]()
    assert parcel["planned_from"] == "2099-09-10T13:00:00+02:00"
    assert parcel["planned_to"] == "2099-09-10T23:59:59+02:00"


def test_pl_returned_to_the_sender_follows_its_status_only():
    parcel = SAMPLES["pl_returned"]()
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False
    assert parcel["delivered_at"] is None


def test_pl_expired_pickup_is_returning_but_not_yet_delivered():
    parcel = SAMPLES["pl_expired"]()
    assert parcel["status"] is ParcelStatus.RETURNING
    assert parcel["delivered"] is False


def test_uk_collected_names_the_point_by_its_code():
    assert SAMPLES["uk_collected"]()["pickup_point"] == "S1"


def test_at_names_the_point_after_collection():
    assert SAMPLES["at_collected"]()["pickup_point"] == "Example Shop"


def test_at_waiting_at_a_shop_has_no_window():
    parcel = SAMPLES["at_at_pickup"]()
    assert parcel["status"] is ParcelStatus.AT_PICKUP_POINT
    assert parcel["planned_from"] is None


def test_de_keeps_an_empty_history_when_the_option_is_on():
    assert SAMPLES["de_active"]()["history"] != []
    assert SAMPLES["de_active"]()["history"] is not None


def test_de_pickup_survives_collection():
    parcel = _de(
        "Paket zugestellt",
        4,
        [
            ("Paketinfo an DPD übergeben", "15.10."),
            ("Paket unterwegs", "16.10."),
            ("Paket im Paketshop zur Abholung bereit", "17.10."),
            ("-", ""),
            ("Paket zugestellt", "18.10."),
        ],
    )()
    assert parcel["status"] is ParcelStatus.DELIVERED
    assert parcel["delivered"] is True
    assert parcel["pickup"] is True


def test_de_home_delivery_is_not_pickup():
    assert SAMPLES["de_delivered"]()["pickup"] is False


def test_at_shop_entry_on_a_sent_parcel_is_not_pickup():
    parcel = _at(4, [_EARLY_SHOP, _at_entry()], direction="send")()
    assert parcel["status"] is ParcelStatus.OUT_FOR_DELIVERY
    assert parcel["pickup"] is False
    assert parcel["pickup_point"] is None


def test_at_shop_entry_on_a_returned_parcel_is_not_pickup():
    parcel = _at(4, [_EARLY_SHOP, _at_entry()], isRetoure=True)()
    assert parcel["pickup"] is False


def test_at_equal_predict_times_are_one_moment():
    parcel = _at(4, [_at_entry()], predict=["10:00", "10:00"])()
    assert parcel["planned_from"] == "2099-10-20T10:00:00+02:00"
    assert parcel["planned_to"] == "2099-10-20T23:59:59+02:00"


def test_at_reversed_predict_times_are_swapped():
    parcel = _at(4, [_at_entry()], predict=["12:00", "10:00"])()
    assert parcel["planned_from"] == "2099-10-20T10:00:00+02:00"
    assert parcel["planned_to"] == "2099-10-20T12:00:00+02:00"


def test_uk_start_without_a_usable_end_covers_the_whole_day():
    parcel = _uk(estimatedDeliveryStartTime=None, estimatedDeliveryEndTime=None)()
    assert parcel["planned_from"] == "2099-10-20T00:00:00+01:00"
    assert parcel["planned_to"] == "2099-10-20T23:59:59+01:00"


def test_general_utc_moment_ends_on_the_parcels_local_day():
    parcel = normalize_parcel(
        shipment_sample(
            "PARCEL_OUT_FOR_DELIVERY",
            event_dt="2099-10-20T10:00:00",
            delivery_date="2099-10-20",
        )
    )
    assert parcel["planned_to"].endswith("23:59:59+02:00")


@pytest.mark.parametrize(
    ("start", "end", "expected"),
    [
        (
            "2026-04-29T13:00:00+02:00",
            "2026-04-29T15:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T15:00:00+02:00"),
        ),
        (
            "2026-04-29T13:00:00+02:00",
            None,
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29",
            None,
            ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            "2026-04-29",
            "2026-05-01",
            ("2026-04-29T00:00:00+02:00", "2026-05-01T23:59:59+02:00"),
        ),
        (
            "2026-04-29T15:00:00+02:00",
            "2026-04-29T13:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T15:00:00+02:00"),
        ),
        (
            None,
            "2026-04-29T13:00:00+02:00",
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            datetime(2026, 4, 29, 13, 0),
            None,
            ("2026-04-29T13:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (
            date(2026, 4, 29),
            date(2026, 4, 29),
            ("2026-04-29T00:00:00+02:00", "2026-04-29T23:59:59+02:00"),
        ),
        (None, None, (None, None)),
        ("garbage", None, (None, None)),
        (12, 13, (None, None)),
    ],
)
def test_planned_window_shapes(start, end, expected):
    assert planned_window(start, end, zone=_AMS) == expected


@pytest.mark.parametrize(
    "status",
    [ParcelStatus.AT_PICKUP_POINT, ParcelStatus.RETURNING, ParcelStatus.DELIVERED],
)
def test_planned_for_status_empties_the_window(status):
    assert planned_for_status(status, "a", "b") == (None, None)


def test_planned_for_status_keeps_the_window_otherwise():
    for status in (
        ParcelStatus.REGISTERED,
        ParcelStatus.IN_TRANSIT,
        ParcelStatus.OUT_FOR_DELIVERY,
        ParcelStatus.PROBLEM,
        ParcelStatus.UNKNOWN,
    ):
        assert planned_for_status(status, "a", "b") == ("a", "b")


def test_is_pickup_sources():
    assert is_pickup(True, ParcelStatus.IN_TRANSIT) is True
    assert is_pickup(None, ParcelStatus.AT_PICKUP_POINT) is True
    assert is_pickup(None, ParcelStatus.DELIVERED, [None, ParcelStatus.AT_PICKUP_POINT])
    assert is_pickup(False, ParcelStatus.IN_TRANSIT, [ParcelStatus.IN_TRANSIT]) is False
    assert is_pickup(None, ParcelStatus.DELIVERED) is False
