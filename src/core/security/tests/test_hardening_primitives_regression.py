"""Regressions for two parser differentials in the shared hardening primitives.

1. ``reject_control_characters`` used to cover only C0 + DEL. C1 controls
   (notably U+0085 NEL) and the Unicode U+2028/U+2029 line separators are line
   breaks for many HTTP stacks, proxies and log shippers, so a URL or token
   carrying them could split a header or forge a log line even after passing
   the check.

2. ``bounded_int`` delegated string parsing to ``int()``, which accepts
   Unicode decimal digits and PEP 515 underscores. Those tokens are rejected by
   ASCII-only validators elsewhere, so the same id could be accepted here and
   interpreted differently by the next hop.
"""

import pytest

from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    content_disposition,
    parse_id,
    reject_control_characters,
    require_http_url,
    require_https_url,
    require_ws_url,
    sanitize_header_filename,
)

pytestmark = [pytest.mark.unit, pytest.mark.security]

# Characters the historical [\x00-\x1f\x7f] pattern missed but that are line
# breaks / controls for at least one downstream parser.
WIDE_CONTROLS = [
    "\x80",  # C1
    "\x85",  # NEL -- NEXT LINE
    "\x9f",  # C1
    "\u2028",  # LINE SEPARATOR
    "\u2029",  # PARAGRAPH SEPARATOR
]


@pytest.mark.parametrize("control", WIDE_CONTROLS)
def test_reject_control_characters_covers_c1_and_unicode_separators(control):
    with pytest.raises(HardeningViolation):
        reject_control_characters(f"pre{control}post", "field")


@pytest.mark.parametrize("control", WIDE_CONTROLS)
@pytest.mark.parametrize(
    "helper", (require_http_url, require_https_url, require_ws_url)
)
def test_url_helpers_reject_wide_control_characters(helper, control):
    # The character is embedded in the authority/path, where urlparse happily
    # leaves it in place; the helper must refuse before parsing.
    with pytest.raises(HardeningViolation):
        helper(f"https://example.test/{control}x", "url")


@pytest.mark.parametrize("control", WIDE_CONTROLS)
def test_header_filename_scrubs_wide_control_characters(control):
    cleaned = sanitize_header_filename(f"report{control}2026.txt")
    assert control not in cleaned
    assert "report" in cleaned


def test_content_disposition_with_unicode_separator_stays_single_line():
    value = content_disposition("report\u2028X-Injected: 1.txt")
    assert "\u2028" not in value
    assert "\n" not in value and "\r" not in value
    assert value.count('"') == 2


@pytest.mark.parametrize("value", ["٤٢", "４２", "1_0", "1٠0", "1\u20282"])
def test_bounded_int_rejects_permissive_int_string_grammar(value):
    with pytest.raises(HardeningViolation):
        bounded_int(value, "id", minimum=0, maximum=100)
    with pytest.raises(HardeningViolation):
        parse_id(value, "id")


@pytest.mark.parametrize("value", ["42", " 42 ", "+7", "-5", "0"])
def test_bounded_int_still_accepts_plain_ascii_integers(value):
    assert bounded_int(value, "value", minimum=-10, maximum=100) == int(value)
