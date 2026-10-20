"""Reading the pages of the myDPD web account.

Everything is read by server-control id, never by position or layout. The
recipient-postcode gate, ``hidZipCode`` and every address block are
deliberately not read here.
"""
from __future__ import annotations

import html as html_lib
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ...const import DpdApiError

_LOGGER = logging.getLogger(__name__)
_ISSUE_URL = (
    "https://github.com/ha-parcel-integrations/ha-dpd/issues/new"
    "?template=unrecognised_status.yml"
)

# ---------------------------------------------------------------------------
# Signed-in markers — the only assumptions about what a signed-out page looks
# like. Change them here and nowhere else.
# ---------------------------------------------------------------------------

# my.dpd.de: a control that exists only for a signed-in user.
_LOGGED_IN_MARKER = 'id="modBurgerNavi_btnLogout"'
# portal.dpd.de: the dashboard control that performs the hop. Its sign-out
# wording also appears on the public login page, so it proves nothing.
_PORTAL_HOST = "portal.dpd.de"
_PORTAL_LOGGED_IN_MARKER = "btnHomeMyParcel"


def is_logged_in(html: str) -> bool:
    """Whether a my.dpd.de page belongs to a signed-in session.

    Never decided from the parcel count: a signed-out page renders the same
    empty-inbox text as an empty account.
    """
    return _LOGGED_IN_MARKER in html


def is_portal_logged_in(url: str, html: str) -> bool:
    """Whether the page fetched from the portal belongs to a signed-in session.

    A signed-out portal redirects to the public login page, so the final host
    matters as much as the control.
    """
    return urlparse(url).hostname == _PORTAL_HOST and _PORTAL_LOGGED_IN_MARKER in html


# ---------------------------------------------------------------------------
# Login form
# ---------------------------------------------------------------------------

_FORM_TAG = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
_SITEKEY = re.compile(r'data-sitekey\s*=\s*"[^"]+"', re.IGNORECASE)
_RECAPTCHA_SCRIPT = re.compile(r"recaptcha/(?:api|enterprise)\.js", re.IGNORECASE)
_INLINE_MESSAGE = re.compile(
    r'<(\w+)\b[^>]*\bclass="[^"]*\bdgs-inline-message\b[^"]*"[^>]*>(.*?)</\1>',
    re.IGNORECASE | re.DOTALL,
)


def has_captcha(html: str) -> bool:
    """Whether a login page or login response asks for a captcha.

    The form-class marker is matched on the ``<form>`` tag only: the same
    string sits in an inline style rule of the pristine page, which is not a
    captcha.
    """
    for tag in _FORM_TAG.findall(html):
        if "german-sso-login-form" in tag and "dgs-form-has-captcha" in tag:
            return True
    return bool(_SITEKEY.search(html) or _RECAPTCHA_SCRIPT.search(html))


def login_rejected(html: str) -> bool:
    """Whether the login form came back with a non-empty error message."""
    if 'name="dpg_username"' not in html:
        return False
    return any(_text(inner) for _tag, inner in _INLINE_MESSAGE.findall(html))


# ---------------------------------------------------------------------------
# Form state
# ---------------------------------------------------------------------------

_INPUT = re.compile(r"<input\b([^>]*)>", re.IGNORECASE | re.DOTALL)
_ATTR = re.compile(r"""([\w:-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_SKIPPED_INPUT_TYPES = frozenset(
    {"submit", "button", "image", "checkbox", "radio", "password"}
)
_REQUIRED_STATE = ("__VIEWSTATE", "__EVENTVALIDATION")


class DpdPageError(DpdApiError):
    """A page lacks something every page of its kind carries."""

    def __init__(self, missing: str) -> None:
        """Name the missing piece, never any page content."""
        super().__init__(200)
        self.missing = missing


def _attrs(raw: str) -> dict[str, str]:
    return {
        match.group(1).lower(): html_lib.unescape(
            match.group(2) if match.group(2) is not None else match.group(3)
        )
        for match in _ATTR.finditer(raw)
    }


def form_fields(html: str) -> dict[str, str]:
    """Every input of a page, to be echoed back by the next postback.

    Each postback must carry the form state of the page it is posted from.
    """
    fields: dict[str, str] = {}
    for match in _INPUT.finditer(html):
        attrs = _attrs(match.group(1))
        name = attrs.get("name")
        if not name or attrs.get("type", "text").lower() in _SKIPPED_INPUT_TYPES:
            continue
        fields[name] = attrs.get("value", "")
    for required in _REQUIRED_STATE:
        if required not in fields:
            raise DpdPageError(required)
    return fields


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

_OPENING_TAG = re.compile(r"<([A-Za-z][\w:-]*)\b([^>]*)>")
_VOID_TAGS = frozenset({"input", "img", "br", "hr", "meta", "link"})
_STRIP_TAGS = re.compile(r"<[^>]*>")
_WHITESPACE = re.compile(r"\s+")


def _text(fragment: str) -> str:
    """Tag-free, unescaped, whitespace-collapsed text of an HTML fragment."""
    stripped = _STRIP_TAGS.sub(" ", fragment)
    return _WHITESPACE.sub(" ", html_lib.unescape(stripped)).strip()


@dataclass(frozen=True)
class Control:
    """One server control: its attributes and its collapsed inner text."""

    attrs: dict[str, str]
    text: str


class Page:
    """A fetched page, indexed by element id."""

    def __init__(self, html: str) -> None:
        """Index every element that carries an id."""
        self.html = html
        self._controls: dict[str, Control] = {}
        lowered = html.lower()
        for match in _OPENING_TAG.finditer(html):
            attrs = _attrs(match.group(2))
            element_id = attrs.get("id")
            if not element_id or element_id in self._controls:
                continue
            tag = match.group(1).lower()
            if tag in _VOID_TAGS:
                inner = ""
            else:
                end = lowered.find(f"</{tag}", match.end())
                inner = html[match.end() : end if end != -1 else match.end()]
            self._controls[element_id] = Control(attrs, _text(inner))

    @property
    def ids(self) -> list[str]:
        """Every element id on the page."""
        return list(self._controls)

    def control(self, element_id: str) -> Control | None:
        """Return one control, or ``None`` when the page has no such id."""
        return self._controls.get(element_id)

    def text(self, element_id: str) -> str | None:
        """Return a control's text."""
        control = self._controls.get(element_id)
        return control.text if control else None

    def value(self, element_id: str) -> str | None:
        """Return a control's ``value`` attribute."""
        control = self._controls.get(element_id)
        return control.attrs.get("value", "") if control else None

    def counter(self, tab: str) -> int | None:
        """Return the parcel count shown on a tab, ``None`` when unreadable."""
        element_id = f"{_PREFIX}counterGet{tab.capitalize()}Parcel"
        text = self.text(element_id)
        if text is None:
            _warn_missing(element_id)
        return int(text) if text is not None and text.isdigit() else None


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------

_PREFIX = "ContentPlaceHolder1_"
_ROW_PREFIX = f"{_PREFIX}repParcelList_"
SLOTS = ("Start", "OnTheRoad", "DeliveryDepot", "CarLoad", "Delivered")
_ICON = re.compile(r"status_(\d+)\.svg")
_MISSING_LOGGED: set[str] = set()
_PAGER_LIKE = re.compile(
    r"pager|_(?:btn|lnk)(?:next|more|loadmore|showmore)\w*$", re.IGNORECASE
)
_PAGER_LOGGED: set[str] = set()


def _warn_missing(control_id: str) -> None:
    """One-shot: a control every inbox page should carry is absent."""
    if control_id in _MISSING_LOGGED:
        return
    _MISSING_LOGGED.add(control_id)
    _LOGGER.warning(
        "A DPD Germany parcel page is missing an expected field (%s) — report "
        "it at %s",
        control_id,
        _ISSUE_URL,
    )


def warn_pager(page: Page) -> None:
    """One-shot: the page offers a way to load more parcels."""
    if _PAGER_LOGGED:
        return
    for element_id in page.ids:
        if element_id.startswith(_PREFIX) and _PAGER_LIKE.search(element_id):
            _PAGER_LOGGED.add(element_id)
            _LOGGER.warning(
                "The DPD Germany parcel list seems to have more pages than "
                "are read — report it at %s",
                _ISSUE_URL,
            )
            return


def _icon(control: Control | None) -> int | None:
    """Return the ``N`` of an ``images/status_<N>.svg`` source."""
    match = _ICON.search(control.attrs.get("src", "")) if control else None
    return int(match.group(1)) if match else None


def list_rows(page: Page) -> list[dict[str, Any]]:
    """Return the parcel rows of the current tab.

    Rows are numbered from 0 and the loop ends at the first absent one; there
    is no fixed cap.
    """
    rows: list[dict[str, Any]] = []
    index = 0
    while (number := page.control(f"{_ROW_PREFIX}labParcelNo_{index}")) is not None:
        sentence = page.text(f"{_ROW_PREFIX}labDeliveryStatus_{index}")
        if sentence is None:
            _warn_missing(f"{_ROW_PREFIX}labDeliveryStatus")
        icon_control = page.control(f"{_ROW_PREFIX}imgDeliverStatus_{index}")
        if icon_control is None:
            _warn_missing(f"{_ROW_PREFIX}imgDeliverStatus")
        if number.text:
            rows.append(
                {
                    "index": index,
                    "barcode": number.text,
                    "identity": page.value(f"{_ROW_PREFIX}hidParcelNo_{index}"),
                    "label": page.text(f"{_ROW_PREFIX}labParcelName_{index}"),
                    "status_sentence": sentence or "",
                    "status_icon": _icon(icon_control),
                }
            )
        index += 1
    return rows


def detail(page: Page) -> dict[str, Any] | None:
    """Return the selected parcel's detail panel, or ``None`` without one.

    ``hidZipCode`` is not read: it holds the recipient postcode.
    """
    identity = page.value(f"{_PREFIX}hidParcelNo")
    if identity is None:
        _warn_missing(f"{_PREFIX}hidParcelNo")
        return None
    slots = []
    for slot in SLOTS:
        text = page.text(f"{_PREFIX}labStatus{slot}")
        date = page.text(f"{_PREFIX}labStatus{slot}Date")
        if text is None or date is None:
            _warn_missing(f"{_PREFIX}labStatus{slot}")
        slots.append({"slot": slot, "text": text or "", "date": date or ""})
    return {
        "identity": identity,
        "slots": slots,
        "status_icon": _icon(page.control(f"{_PREFIX}imgParcelStatus")),
        "weight_text": page.value(f"{_PREFIX}hidWeight") or "",
        "flow_type": page.value(f"{_PREFIX}hidParcelFlowTypeID") or "",
        "ucid": page.value(f"{_PREFIX}hidUCID") or "",
    }
