"""Regressions for two gaps between the shared URL/text primitives and the
request-schema boundary.

1. ``reject_control_characters`` covered C0/DEL/C1 and the Unicode line
   separators but not the bidi / invisible-format characters that
   ``policy_types.UNSAFE_UNICODE`` rejects at the schema boundary. Callers that
   use the hardening helpers directly (the SSRF gate, federation endpoints,
   core boundaries) therefore accepted a URL or filename that rendered as a
   different value than the one that was stored and fetched.

2. ``parse_outbound_url`` is the single gate every stored outbound URL passes
   through, but it did not refuse embedded ``user:pass@`` credentials even
   though ``require_http_url`` and ``validate_http_url`` do. A caller going
   straight to the gate could persist a URL whose secret is echoed to clients,
   written to logs and replayed on fetch.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.core.security.errors import OutboundUrlRejected
from src.core.security.hardening import (
    HardeningViolation,
    reject_control_characters,
    require_http_url,
    require_https_url,
    require_ws_url,
    sanitize_header_filename,
)
from src.core.security.hardening.text import (
    CONTROL_CHARACTERS,
    strip_control_characters,
)
from src.core.security.outbound import parse_outbound_url, resolve_outbound_url

pytestmark = [pytest.mark.unit, pytest.mark.security]

# Invisible / bidi formatting characters that are Not part of C0/C1
# (0x00-0x1f, 0x7f-0x9f) or the line separators, but that the request-schema
# policy already treats as unsafe because they change how a value renders.
INVISIBLE_CONTROLS = [
    "\u061c",  # ARABIC LETTER MARK
    "\u200e",  # LEFT-TO-RIGHT MARK
    "\u200f",  # RIGHT-TO-LEFT MARK
    "\u070f",  # SYRIAC ABBREVIATION MARK (strong RTL)
    "\u202a",  # LEFT-TO-RIGHT EMBEDDING
    "\u202b",  # RIGHT-TO-LEFT EMBEDDING
    "\u202c",  # POP DIRECTIONAL FORMATTING
    "\u202d",  # LEFT-TO-RIGHT OVERRIDE
    "\u202e",  # RIGHT-TO-LEFT OVERRIDE
    "\u2066",  # LEFT-TO-RIGHT ISOLATE
    "\u2067",  # RIGHT-TO-LEFT ISOLATE
    "\u2068",  # FIRST STRONG ISOLATE
    "\u2069",  # POP DIRECTIONAL ISOLATE
    "\u206a",  # INHIBIT SYMMETRIC SWAPPING
    "\u206f",  # NOMINAL DIGIT SHAPES
    "\u2060",  # WORD JOINER
    "\ufeff",  # ZERO WIDTH NO-BREAK SPACE / BOM
]


# --- 1. bidi / invisible controls in the shared primitive --------------------


@pytest.mark.parametrize("control", INVISIBLE_CONTROLS)
def test_reject_control_characters_covers_invisible_bidi_controls(control):
    with pytest.raises(HardeningViolation):
        reject_control_characters(f"pre{control}post", "field")


@pytest.mark.parametrize("control", INVISIBLE_CONTROLS)
def test_control_pattern_matches_each_invisible_control(control):
    assert CONTROL_CHARACTERS.search(control)


@pytest.mark.parametrize("control", INVISIBLE_CONTROLS)
@pytest.mark.parametrize(
    "helper", (require_http_url, require_https_url, require_ws_url)
)
def test_url_helpers_reject_invisible_controls(helper, control):
    # An override/isolate in a host makes the URL render as a different host
    # than the one that was validated and later fetched.
    with pytest.raises(HardeningViolation):
        helper(f"https://exa{control}mple.example/x", "url")


@pytest.mark.parametrize("control", INVISIBLE_CONTROLS)
def test_header_filename_scrubs_invisible_controls(control):
    assert control not in sanitize_header_filename(f"report{control}2026.txt")
    assert control not in strip_control_characters(f"report{control}2026.txt")


def test_zero_width_joiner_is_not_over_blocked():
    # U+200D is a legitimate part of emoji sequences; the schema policy keeps
    # it too, so the shared primitive must not start rejecting it.
    value = "caf\u00e9 \U0001f468\u200d\U0001f469\u200d\U0001f467"
    assert reject_control_characters(value, "field") == value
    assert not CONTROL_CHARACTERS.search("\u200d")


def test_plain_values_still_pass():
    assert reject_control_characters("abc123-_./", "field") == "abc123-_./"


# --- 2. embedded credentials at the outbound gate ---------------------------


CREDENTIALED_URLS = [
    "https://user@example.example/x",
    "https://user:pass@example.example/x",
    "https://:pass@example.example/x",
    "http://u:p@example.example/a?b=1#f",
]


@pytest.mark.parametrize("url", CREDENTIALED_URLS)
def test_outbound_gate_refuses_credentials_before_dns(url):
    with patch("src.core.security.outbound.URLValidator") as validator:
        with pytest.raises(OutboundUrlRejected) as rejection:
            parse_outbound_url(url, "remote_url")

    validator.return_value.validate_url_for_request.assert_not_called()
    assert "credentials" in str(rejection.value)
    assert "remote_url" in str(rejection.value)


@pytest.mark.parametrize("url", CREDENTIALED_URLS)
def test_resolve_outbound_url_also_refuses_credentials(url):
    with patch("src.core.security.outbound.URLValidator") as validator:
        with pytest.raises(OutboundUrlRejected):
            resolve_outbound_url(url, "Server icon URL")
    validator.return_value.validate_url_for_request.assert_not_called()


def test_credential_free_url_still_passes_the_gate():
    with patch("src.core.security.outbound.URLValidator") as validator:
        validator.return_value.validate_url_for_request.return_value = (
            "example.example",
            "203.0.113.9",
        )
        result = parse_outbound_url("https://example.example/x", "remote_url")
    assert result.url == "https://example.example/x"
    assert result.hostname == "example.example"
    assert result.address == "203.0.113.9"
