"""Tests for DPD Germany's page reader (synthetic pages only)."""
import logging

import pytest

from custom_components.dpd.countries.de import page as de_page
from custom_components.dpd.countries.de.page import (
    DpdPageError,
    Page,
    detail,
    form_fields,
    has_captcha,
    is_logged_in,
    is_portal_logged_in,
    list_rows,
    login_rejected,
    warn_pager,
)

from ..payloads import (
    DE_BARCODE,
    DE_BARCODE_2,
    DE_POSTCODE,
    de_inbox_page,
    de_login_page,
    de_portal_page,
    de_slots,
)

PORTAL = "https://portal.dpd.de/home_pn.aspx"
_ROW = {
    "barcode": DE_BARCODE,
    "label": "Mein Paket",
    "sentence": "Paket in Zustellung",
    "icon": 4,
}


# -- markers ----------------------------------------------------------------


def test_signed_in_is_decided_by_the_logout_control_not_by_the_row_count():
    assert is_logged_in(de_inbox_page([_ROW]))
    assert is_logged_in(de_inbox_page([]))
    assert not is_logged_in(de_inbox_page([_ROW], signed_in=False))


def test_portal_marker():
    assert is_portal_logged_in(PORTAL, de_portal_page())
    # "Abmelden" is on the public login page too; it must not count.
    assert "Abmelden" in de_portal_page(signed_in=False)
    assert not is_portal_logged_in(PORTAL, de_portal_page(signed_in=False))
    # The control on a page that is not the portal's does not count either.
    assert not is_portal_logged_in("https://www.dpd.com/de/de/login", de_portal_page())


# -- captcha ----------------------------------------------------------------


def test_the_pristine_login_page_is_not_a_captcha():
    """The CSS rule names the same class; it must not count."""
    assert not has_captcha(de_login_page())


@pytest.mark.parametrize(
    "page",
    [
        de_login_page(form_class="dgs-form-has-captcha"),
        de_login_page(captcha='<div data-sitekey="abc"></div>'),
        de_login_page(
            captcha='<script src="https://www.google.com/recaptcha/api.js"></script>'
        ),
        de_login_page(
            captcha='<script src="https://www.google.com/recaptcha/enterprise.js"></script>'
        ),
    ],
)
def test_each_captcha_marker_alone_is_detected(page):
    assert has_captcha(page)


def test_a_captcha_class_on_another_form_is_not_the_login_form():
    other = '<form id="search" class="dgs-form-has-captcha"></form>'
    assert not has_captcha(f"<html>{other}</html>")


def test_login_rejection_needs_the_form_and_a_non_empty_message():
    message = '<div class="dgs-inline-message"><p>Falsche Zugangsdaten</p></div>'
    assert login_rejected(de_login_page(message=message))
    empty = '<div class="dgs-inline-message"><p> </p></div>'
    assert not login_rejected(de_login_page(message=empty))
    assert not login_rejected(de_login_page())
    assert not login_rejected(f"<html>{message}</html>")


# -- form state -------------------------------------------------------------


def test_form_fields_echo_hidden_and_text_inputs_only():
    fields = form_fields(de_portal_page())
    assert fields["__VIEWSTATE"] == "VS-P"
    assert fields["__EVENTVALIDATION"] == "EV-P"
    assert fields["ctl00$cphBody$ctl00$txtSearch"] == ""
    assert "ctl00$cphBody$ctl00$btnGo" not in fields
    assert "ctl00$cphBody$ctl00$pw" not in fields
    assert "ctl00$cphBody$ctl00$chk" not in fields
    assert all(name for name in fields)


def test_form_fields_unescape_values():
    html = (
        '<input type="hidden" name="__VIEWSTATE" value="a&amp;b" />'
        "<input type='hidden' name='__EVENTVALIDATION' value='c' />"
    )
    assert form_fields(html) == {"__VIEWSTATE": "a&b", "__EVENTVALIDATION": "c"}


@pytest.mark.parametrize("missing", ["__VIEWSTATE", "__EVENTVALIDATION"])
def test_a_page_without_form_state_is_a_page_error(missing):
    html = de_portal_page().replace(f'name="{missing}"', 'name="other"')
    with pytest.raises(DpdPageError) as err:
        form_fields(html)
    assert err.value.missing == missing
    assert err.value.status_code == 200


# -- controls ---------------------------------------------------------------


def test_controls_are_read_by_id_with_collapsed_text():
    page = Page(
        '<div id="a"> one <b>two</b>\n  three &amp; four </div>'
        '<span id="a">ignored duplicate</span>'
        '<input id="v" value="x &amp; y" />'
        '<span id="open">never closed'
    )
    assert page.text("a") == "one two three & four"
    assert page.value("v") == "x & y"
    assert page.text("open") == ""
    assert page.control("nope") is None
    assert page.text("nope") is None
    assert page.value("nope") is None
    assert set(page.ids) == {"a", "v", "open"}


def test_counters():
    page = Page(de_inbox_page([_ROW], counters=(1, 0, 7)))
    assert page.counter("incoming") == 1
    assert page.counter("sending") == 0
    assert page.counter("returning") == 7


def test_a_missing_counter_is_none_and_warns_once(caplog):
    page = Page("<html></html>")
    with caplog.at_level(logging.WARNING):
        assert page.counter("sending") is None
        assert page.counter("sending") is None
    assert [r.getMessage().count("counterGetSendingParcel") for r in caplog.records] == [1]


# -- rows -------------------------------------------------------------------


def test_rows_are_read_until_the_first_absent_one_with_no_cap():
    rows = [
        {**_ROW, "barcode": f"0123456789{index:04d}"} for index in range(12)
    ]
    parsed = list_rows(Page(de_inbox_page(rows)))
    assert len(parsed) == 12
    assert parsed[11]["barcode"] == "01234567890011"
    assert parsed[11]["index"] == 11


def test_a_row_exposes_its_list_fields():
    (row,) = list_rows(Page(de_inbox_page([_ROW])))
    assert row == {
        "index": 0,
        "barcode": DE_BARCODE,
        "identity": DE_BARCODE,
        "label": "Mein Paket",
        "status_sentence": "Paket in Zustellung",
        "status_icon": 4,
    }


def test_a_row_without_a_number_is_skipped_but_iteration_continues():
    html = de_inbox_page([_ROW, {**_ROW, "barcode": DE_BARCODE_2}]).replace(
        f'class="parcelNo">{DE_BARCODE}<', 'class="parcelNo"><'
    )
    assert [r["barcode"] for r in list_rows(Page(html))] == [DE_BARCODE_2]


def test_missing_row_controls_warn_once(caplog):
    html = (
        '<span id="ContentPlaceHolder1_repParcelList_labParcelNo_0">'
        f"{DE_BARCODE}</span>"
    )
    with caplog.at_level(logging.WARNING):
        (row,) = list_rows(Page(html))
        list_rows(Page(html))
    assert row["status_sentence"] == ""
    assert row["status_icon"] is None
    assert row["label"] is None
    assert row["identity"] is None
    messages = [r.getMessage() for r in caplog.records]
    assert len(messages) == 2
    assert any("labDeliveryStatus" in m for m in messages)
    assert any("imgDeliverStatus" in m for m in messages)


def test_an_unparseable_icon_is_none():
    html = de_inbox_page([_ROW]).replace("status_4.svg", "weird.svg")
    assert list_rows(Page(html))[0]["status_icon"] is None


# -- detail -----------------------------------------------------------------


def test_detail_reads_the_panel_and_never_the_postcode():
    page = Page(
        de_inbox_page(
            [_ROW],
            detail={
                "barcode": DE_BARCODE,
                "slots": de_slots(("Paketinfo an DPD übergeben", "01.10.")),
                "weight": "10,95",
                "flow_type": "0",
                "icon": 2,
            },
        )
    )
    panel = detail(page)
    assert panel["identity"] == DE_BARCODE
    assert panel["weight_text"] == "10,95"
    assert panel["flow_type"] == "0"
    assert panel["ucid"] == ""
    assert panel["status_icon"] == 2
    assert panel["slots"][0] == {
        "slot": "Start",
        "text": "Paketinfo an DPD übergeben",
        "date": "01.10.",
    }
    assert panel["slots"][4] == {"slot": "Delivered", "text": "-", "date": ""}
    assert DE_POSTCODE not in repr(panel)


def test_no_panel_is_none_and_warns_once(caplog):
    with caplog.at_level(logging.WARNING):
        assert detail(Page(de_inbox_page([_ROW]))) is None
        assert detail(Page(de_inbox_page([_ROW]))) is None
    assert len(caplog.records) == 1


def test_a_panel_missing_a_milestone_control_warns(caplog):
    page = Page(
        '<input id="ContentPlaceHolder1_hidParcelNo" value="x" />'
    )
    with caplog.at_level(logging.WARNING):
        panel = detail(page)
    assert panel["slots"][0] == {"slot": "Start", "text": "", "date": ""}
    assert panel["weight_text"] == ""
    assert any("labStatusStart" in r.getMessage() for r in caplog.records)


# -- pager ------------------------------------------------------------------


def test_a_pager_like_control_warns_once(caplog):
    page = Page(de_inbox_page([_ROW], extra='<a id="ContentPlaceHolder1_btnNextPage"></a>'))
    with caplog.at_level(logging.WARNING):
        warn_pager(page)
        warn_pager(page)
    assert len(caplog.records) == 1


def test_the_real_page_ids_do_not_look_like_a_pager(caplog):
    page = Page(
        de_inbox_page([_ROW], extra='<span id="ContentPlaceHolder1_labMoreDetailsHead"></span>')
    )
    with caplog.at_level(logging.WARNING):
        warn_pager(page)
    assert caplog.records == []
    assert de_page._PAGER_LOGGED == set()
