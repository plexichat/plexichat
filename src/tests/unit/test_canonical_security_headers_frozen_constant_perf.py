"""Hot-path regression: the canonical security-header map is a frozen constant.

``SecurityHeadersMiddleware`` stamps *every* HTTP response through
``enforce_response_policy -> merge_security_headers -> canonical_security_headers``.
That last helper used to build a brand-new list of four/five ``(name, value)``
tuples on every single call, i.e. on every request. The values are immutable,
so a module-level frozen tuple is correct and removes the per-request churn.

These tests fail on the old (per-call allocation) implementation.
"""

from __future__ import annotations

import tracemalloc

from src.api.middleware.security_header_policy import canonical_security_headers

_EXPECTED = (
    (b"X-Content-Type-Options", b"nosniff"),
    (b"Referrer-Policy", b"strict-origin-when-cross-origin"),
    (b"X-Permitted-Cross-Domain-Policies", b"none"),
    (b"X-XSS-Protection", b"1; mode=block"),
    (b"X-Frame-Options", b"SAMEORIGIN"),
)


def test_canonical_headers_are_exact_and_immutable():
    """The constant must preserve the exact, fail-closed header set."""
    assert tuple(canonical_security_headers()) == _EXPECTED
    assert tuple(canonical_security_headers(include_frame=False)) == _EXPECTED[:-1]
    # Frozen: callers cannot mutate the shared per-process constant.
    assert isinstance(canonical_security_headers(), tuple)


def test_canonical_headers_are_cached_not_rebuilt():
    """Repeated calls must hand back the same immutable object."""
    assert canonical_security_headers() is canonical_security_headers()
    assert canonical_security_headers(
        include_frame=False
    ) is canonical_security_headers(include_frame=False)


def test_canonical_headers_no_per_call_allocation():
    """No heap allocation may occur while reading the canonical map.

    This is the direct proof of the hot-path fix: before it, each call built
    a list plus 4-5 tuples, so the traced peak over the loop was > 0 bytes.
    After it, calls only return a pre-built module constant: peak stays 0.
    """
    # Warm up so the constants themselves are allocated before tracing starts.
    canonical_security_headers()
    canonical_security_headers(include_frame=False)

    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        for _ in range(2000):
            canonical_security_headers()
            canonical_security_headers(include_frame=False)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert peak == 0, (
        "canonical_security_headers allocates on every request; "
        f"peak transient allocation was {peak} bytes over 2000 calls"
    )
