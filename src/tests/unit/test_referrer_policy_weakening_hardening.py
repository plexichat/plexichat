"""Regression: upstream handlers must not weaken the canonical Referrer-Policy.

``security_header_policy`` is the last word on browser security decisions. Its
canonical ``Referrer-Policy`` is ``strict-origin-when-cross-origin``. Two legacy
values are strictly *weaker* and must be replaced rather than preserved:

* ``no-referrer-when-downgrade`` sends the full URL (path and query, which
  routinely carry tokens and object ids) to any cross-origin HTTPS destination;
  the canonical policy sends only the origin there.
* ``origin-when-cross-origin`` still discloses the origin on an HTTPS->HTTP
  downgrade where the canonical policy sends no referrer at all.

Keeping either value in the "safe to keep" set let an upstream handler (or a
reflected request value) silently downgrade the referrer boundary.
"""

import pytest

from src.api.middleware.response_policy import enforce_response_policy
from src.api.middleware.security_header_audit import (
    audit_security_headers,
    strip_unsafe_headers,
)
from src.api.middleware.security_header_policy import (
    CANONICAL_HEADERS,
    is_weakened,
    merge_security_headers,
)

CANONICAL = CANONICAL_HEADERS[b"referrer-policy"]
WEAKER_VALUES = (b"no-referrer-when-downgrade", b"origin-when-cross-origin")
STRONGER_VALUES = (b"no-referrer", b"origin", b"same-origin", b"strict-origin")


@pytest.mark.parametrize("value", WEAKER_VALUES)
def test_weaker_referrer_values_are_detected(value):
    assert is_weakened(b"Referrer-Policy", value) is True


@pytest.mark.parametrize("value", WEAKER_VALUES)
def test_merge_replaces_weaker_referrer_value(value):
    merged = merge_security_headers([(b"Referrer-Policy", value)])
    values = [v for k, v in merged if k.lower() == b"referrer-policy"]
    assert values == [CANONICAL]


@pytest.mark.parametrize("value", STRONGER_VALUES)
def test_merge_preserves_stronger_referrer_value(value):
    merged = merge_security_headers([(b"Referrer-Policy", value)])
    values = [v for k, v in merged if k.lower() == b"referrer-policy"]
    assert values == [value]


@pytest.mark.parametrize("value", WEAKER_VALUES)
def test_response_policy_emits_canonical_referrer(value):
    """End-to-end: the stamped response carries the canonical, not the weak value."""
    final = enforce_response_policy(
        [(b"Referrer-Policy", value)],
        path="/api/v1/users",
        api_prefix="/api/v1",
    )
    values = [v for k, v in final if k.lower() == b"referrer-policy"]
    assert values == [CANONICAL]


@pytest.mark.parametrize("value", WEAKER_VALUES)
def test_audit_and_strip_agree_referrer_is_unsafe(value):
    """The audit helpers share is_weakened; they must flag the same values."""
    issues = audit_security_headers([(b"Referrer-Policy", value)])
    assert "weakened:referrer-policy" in issues
    assert (b"Referrer-Policy", value) not in strip_unsafe_headers(
        [(b"Referrer-Policy", value)]
    )


def test_canonical_value_itself_is_never_weakened():
    assert is_weakened(b"Referrer-Policy", b"strict-origin-when-cross-origin") is False
