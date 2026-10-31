"""Every language file carries the same config-flow error keys."""

import json
from pathlib import Path

import pytest

_DIR = Path(__file__).parent.parent / "custom_components" / "dpd"
_FILES = [_DIR / "strings.json", *sorted((_DIR / "translations").glob("*.json"))]


@pytest.mark.parametrize("path", _FILES, ids=lambda path: path.name)
def test_the_captcha_error_is_translated_everywhere(path):
    errors = json.loads(path.read_text(encoding="utf-8"))["config"]["error"]
    assert errors["captcha"].strip()
    assert set(errors) == {
        "cannot_connect",
        "captcha",
        "email_required",
        "invalid_auth",
        "invalid_phone",
        "invalid_sms_code",
        "link_expired",
        "not_a_consumer",
        "unrecognised_link",
    }
