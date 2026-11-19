"""Regression: whitespace/entity obfuscation must not bypass the SVG serve guard.

``is_unsafe_serve_payload`` is the fail-closed classifier for stored avatar /
icon rows (including legacy or poisoned rows written before the upload guards
existed). HTML/SVG permit arbitrary whitespace around ``=`` and numeric
character references for ``:``; a classifier that only matched the literal
``onload=`` / ``javascript:`` byte strings let a script-carrying SVG be served
raw under the application origin.
"""

import importlib.util
from pathlib import Path

import pytest

_GUARD_PATH = (
    Path(__file__).resolve().parents[2]
    / "core"
    / "avatars"
    / "manager"
    / "svg_guard.py"
)

_spec = importlib.util.spec_from_file_location("_svg_guard_under_test", _GUARD_PATH)
svg_guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(svg_guard)


@pytest.mark.parametrize(
    "payload",
    [
        b"<svg onload =alert(1)></svg>",
        b"<svg onload\t=alert(1)></svg>",
        b"<svg onload\n=alert(1)></svg>",
        b'<svg><img src=x onerror ="alert(1)"></svg>',
        b'<svg><a href="javascript&#58;alert(1)">x</a></svg>',
        b'<svg><a href="javascript&#x3a;alert(1)">x</a></svg>',
    ],
)
def test_whitespace_and_entity_obfuscated_svg_is_unsafe(payload):
    # Labelled openly as SVG and spoofed as a raster type: neither may be
    # served raw. The latter is the poisoned-legacy-row case.
    assert svg_guard.is_unsafe_serve_payload("image/svg+xml", payload) is True
    assert svg_guard.is_unsafe_serve_payload("image/png", payload) is True


def test_plain_literal_handler_still_blocked():
    assert (
        svg_guard.is_unsafe_serve_payload("image/png", b"<svg onload=alert(1)></svg>")
        is True
    )


def test_benign_markup_and_raster_still_allowed():
    # No active constructs: the explicit handler list avoids flagging inert
    # markup or arbitrary binary data.
    assert (
        svg_guard.is_unsafe_serve_payload("image/png", b"<svg><rect/></svg>") is False
    )
    assert (
        svg_guard.is_unsafe_serve_payload(
            "image/png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 40
        )
        is False
    )
