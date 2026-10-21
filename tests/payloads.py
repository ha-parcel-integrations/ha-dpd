"""Sample DPD API payloads shared by the test modules.

The shape of a ``my-parcels`` response, trimmed to the fields the integration
reads. Keep them here rather than inline in each test module: when the payload
shape turns out to differ from what we assumed, there is then exactly one place
to fix.

Every helper returns a **fresh** dict, so a test may mutate what it gets back
without leaking into the next one.
"""

from __future__ import annotations

from collections.abc import Iterable

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


# ---------------------------------------------------------------------------
# DPD Germany web account — hand-built pages with the real server-control ids
# ---------------------------------------------------------------------------

DE_BARCODE = "01234567890123"
DE_BARCODE_2 = "01234567890124"
DE_POSTCODE = "99999"
_P = "ContentPlaceHolder1_"


def de_form_state(token: str = "A") -> str:  # noqa: S107 - placeholder token, not a credential
    """Hidden form state, distinct per ``token`` so a test can tell pages apart."""
    return (
        f'<input type="hidden" name="__VIEWSTATE" id="__VIEWSTATE" value="VS-{token}" />'
        f'<input type="hidden" name="__VIEWSTATEGENERATOR" value="GEN-{token}" />'
        f'<input type="hidden" name="__EVENTVALIDATION" value="EV-{token}" />'
    )


def de_slots(*pairs: tuple[str, str]) -> list[tuple[str, str]]:
    """Five ``(text, date)`` slots; missing trailing ones read as unreached."""
    return list(pairs) + [("-", "")] * (5 - len(pairs))


def de_inbox_page(
    rows: Iterable[dict] = (),
    *,
    token: str = "A",  # noqa: S107 - placeholder token, not a credential
    counters: tuple[int, int, int] | None = None,
    detail: dict | None = None,
    signed_in: bool = True,
    extra: str = "",
) -> str:
    """The signed-in parcel list, optionally with one selected parcel's panel.

    ``rows`` are ``{"barcode", "label", "sentence", "icon"}``; ``detail`` is
    ``{"barcode", "slots", "weight", "flow_type", "icon"}``.
    """
    rows = list(rows)
    counters = counters or (len(rows), 0, 0)
    marker = (
        '<a id="modBurgerNavi_btnLogout" class="btnLogout" href="#">Abmelden</a>'
        if signed_in
        else "<span>Anmelden</span>"
    )
    out = [
        "<html><head><title>Meine Pakete</title></head><body>",
        '<form name="form1" method="post" action="./myParcel.aspx" id="form1">',
        de_form_state(token),
        marker,
    ]
    for tab, count in zip(("Incoming", "Sending", "Returning"), counters, strict=False):
        out.append(
            f'<a id="{_P}btnGet{tab}Parcel"><span id="{_P}counterGet{tab}Parcel" '
            f'class="counterParcelFilter">{count}</span></a>'
        )
    for index, row in enumerate(rows):
        out.append(
            f'<a id="{_P}repParcelList_btnSelectParcel_{index}" href="#">'
            f'<span id="{_P}repParcelList_labParcelName_{index}">{row.get("label", "DPD Paket")}</span>'
            f'<input type="hidden" name="ctl00$ContentPlaceHolder1$repParcelList$ctl{index:02d}$hidParcelNo"'
            f' id="{_P}repParcelList_hidParcelNo_{index}" value="{row["barcode"]}" />'
            f'<span id="{_P}repParcelList_labParcelNo_{index}" class="parcelNo">{row["barcode"]}</span>'
            f'<span id="{_P}repParcelList_labDeliveryStatus_{index}">{row.get("sentence", "")}</span>'
            f'<img id="{_P}repParcelList_imgDeliverStatus_{index}" '
            f'src="images/status_{row.get("icon", 1)}.svg" />'
            "</a>"
        )
    if detail is not None:
        out.append(
            f'<input type="hidden" name="ctl00$ContentPlaceHolder1$hidParcelNo" '
            f'id="{_P}hidParcelNo" value="{detail["barcode"]}" />'
            f'<input type="hidden" id="{_P}hidZipCode" name="ctl00$ContentPlaceHolder1$hidZipCode" '
            f'value="{DE_POSTCODE}" />'
            f'<input type="hidden" id="{_P}hidParcelFlowTypeID" '
            f'name="ctl00$ContentPlaceHolder1$hidParcelFlowTypeID" value="{detail.get("flow_type", "0")}" />'
            f'<input type="hidden" id="{_P}hidUCID" name="ctl00$ContentPlaceHolder1$hidUCID" />'
            f'<input type="hidden" id="{_P}hidWeight" name="ctl00$ContentPlaceHolder1$hidWeight" '
            f'value="{detail.get("weight", "")}" />'
            f'<input type="text" id="{_P}txtPostcode" name="ctl00$ContentPlaceHolder1$txtPostcode" />'
            f'<img id="{_P}imgParcelStatus" src="images/status_{detail.get("icon", 1)}.svg" />'
        )
        for name, (text, date) in zip(
            ("Start", "OnTheRoad", "DeliveryDepot", "CarLoad", "Delivered"),
            detail["slots"],
            strict=False,
        ):
            out.append(
                f'<span id="{_P}labStatus{name}" class="labSub13">{text}</span>'
                f'<span id="{_P}labStatus{name}Date" class="labSub13">{date}</span>'
            )
    out.append(extra)
    out.append("</form></body></html>")
    return "".join(out)


def de_portal_page(*, signed_in: bool = True, token: str = "P") -> str:  # noqa: S107 - placeholder token, not a credential
    """The portal dashboard the hop is posted from."""
    return (
        "<html><body>"
        f'<form method="post" action="./home_pn.aspx">{de_form_state(token)}'
        '<input type="text" name="ctl00$cphBody$ctl00$txtSearch" value="" />'
        '<input type="submit" name="ctl00$cphBody$ctl00$btnGo" value="Go" />'
        '<input type="password" name="ctl00$cphBody$ctl00$pw" value="secret" />'
        '<input type="checkbox" name="ctl00$cphBody$ctl00$chk" value="on" />'
        '<input value="orphan" />'
        f"{'<a id="cphBody_btnHomeMyParcel">Meine Pakete</a>' if signed_in else ''}"
        "<a>Abmelden</a>"
        "</form></body></html>"
    )


def de_login_page(*, form_class: str = "", captcha: str = "", message: str = "") -> str:
    """The login form, as the login POST re-renders it."""
    return (
        "<html><head><style>form.dgs-form-has-captcha .dgs-row-recaptcha "
        "{margin-top: 0.5rem;} .dgs-inline-message {display: none;}</style></head>"
        f'<body>{captcha}<form name="german-sso-login-form" class="{form_class}" '
        'method="POST" action="">'
        '<input name="dpg_username" /><input type="password" name="dpg_password" />'
        f"{message}</form></body></html>"
    )
