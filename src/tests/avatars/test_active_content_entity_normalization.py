"""Regression: HTML character references must not bypass active-content guards.

The media/avatar active-content classifiers are the fail-closed single source
of truth for polyglot and script detection. Browsers decode character
references before applying URL/markup semantics, so a payload carrying
``javascript&colon;...`` or ``onload&Tab;=...`` is script-capable even though
the literal ``javascript:`` / ``onload=`` tokens are absent. The classifiers
must normalise references before matching their token lists.
"""

import pytest

from src.core.avatars.manager import upload_guard, svg_guard

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 48
GIF = b"GIF89a" + b"\x00" * 48


@pytest.mark.parametrize(
    "payload",
    [
        b"x javascript&colon;alert(1) y",
        b"x javascript&#58;alert(1) y",
        b"x javascript&#x3a;alert(1) y",
        b"<svg onload&Tab;=alert(1)>",
        b"GIF89a" + b"javascript&colon;alert(1)",
    ],
)
def test_upload_guard_detects_entity_obfuscated_active_content(payload):
    assert upload_guard.contains_active_content(payload) is True


def test_serve_guard_rejects_entity_obfuscated_polyglot():
    # A GIF container with an entity-obfuscated javascript: URI used to be
    # classified as safe because no literal dangerous token was present.
    payload = GIF + b"javascript&colon;alert(1)"
    assert upload_guard.serve_payload_is_safe("image/gif", payload) is False


@pytest.mark.parametrize(
    "payload",
    [
        b"<svg><a href='javascript&colon;alert(1)'>x</a></svg>",
        b"<svg onload&Tab;=alert(1)>",
        b"<svg><a href='javascript&#58;alert(1)'>x</a></svg>",
    ],
)
def test_svg_guard_detects_entity_obfuscated_active_content(payload):
    assert svg_guard.payload_carries_active_content(payload) is True
    assert svg_guard.is_unsafe_serve_payload("image/svg+xml", payload) is True


@pytest.mark.parametrize(
    "payload",
    [PNG, GIF],
)
def test_benign_raster_payloads_still_allowed(payload):
    assert upload_guard.serve_payload_is_safe("image/png", payload) is True
    assert svg_guard.payload_carries_active_content(payload) is False


@pytest.mark.parametrize(
    "payload",
    [
        b"<svg><rect/></svg>",
        b"<svg><text>AT&amp;T</text></svg>",
    ],
)
def test_benign_markup_has_no_active_content(payload):
    # Markup itself is refused at serve time (defense in depth); here we only
    # assert that entity normalisation did not manufacture a false "active"
    # verdict for inert SVG content.
    assert svg_guard.payload_carries_active_content(payload) is False
