"""Regression: SVG sanitizer must reject SMIL-driven XSS payloads.

``<animate>`` / ``<set>`` mutate attributes at render time, so a payload that
passes the static ``href`` / ``on*`` allow-list can still set a ``javascript:``
URI or an event handler after validation. These must be refused fail-closed.
"""

import pytest

from src.core.media.security.validation import FileValidator


@pytest.mark.parametrize(
    "payload",
    [
        # animate mutating a URI attribute to javascript:
        b'<svg xmlns="http://www.w3.org/2000/svg" '
        b'xmlns:xlink="http://www.w3.org/1999/xlink">'
        b'<use xlink:href="#x"><animate attributeName="xlink:href" '
        b'to="javascript:alert(1)" dur="1s" fill="freeze"/></use>'
        b'<symbol id="x"><rect width="10" height="10"/></symbol></svg>',
        # set creating an event handler attribute
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect width="10" height="10"><set attributeName="onload" '
        b'to="alert(1)"/></rect></svg>',
        # animate targeting a plain href with a script URI
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect width="10" height="10"><animate attributeName="href" '
        b'to="javascript:alert(1)" dur="1s" fill="freeze"/></rect></svg>',
        # values list carrying a script URI
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect width="10" height="10"><animate attributeName="opacity" '
        b'values="javascript:alert(1);0" dur="1s"/></rect></svg>',
    ],
)
def test_smil_animation_payloads_are_rejected(payload):
    ok, reason = FileValidator._sanitize_svg(payload)
    assert ok is False
    assert "unsafe_animation" in reason


@pytest.mark.parametrize(
    "payload",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect width="10" height="10"><animate attributeName="opacity" '
        b'values="0;1;0" dur="1s"/></rect></svg>',
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect width="10" height="10"><animate attributeName="width" '
        b'from="10" to="20" dur="1s"/></rect></svg>',
    ],
)
def test_benign_animation_is_still_accepted(payload):
    ok, reason = FileValidator._sanitize_svg(payload)
    assert ok is True, reason


def test_validator_entrypoint_rejects_smil_xss():
    validator = FileValidator()
    payload = (
        b'<svg xmlns="http://www.w3.org/2000/svg">'
        b'<rect width="10" height="10"><set attributeName="onload" '
        b'to="alert(1)"/></rect></svg>'
    )
    valid, error, _ = validator.validate_file(payload, "x.svg", "image/svg+xml")
    assert valid is False
    assert "Unsafe content detected in SVG file" == error
