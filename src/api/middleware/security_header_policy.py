"""Canonical, tamper-resistant security header policy.

The middleware that stamps security headers must be the *last* word on a
small set of values that browsers treat as security decisions. Leaving them
to "only add if absent" lets any upstream handler -- or a reflected value --
weaken ``nosniff``, set ``Referrer-Policy: unsafe-url``, or allow framing.
This module defines the canonical values and a merge function that replaces
weak/incorrect ones instead of keeping them.
"""

from __future__ import annotations

from typing import Iterable, List

Header = tuple[bytes, bytes]

# Lower-cased names -> canonical byte values. These are the values the rest of
# the codebase relies on; keep them in one place so a CSP/header change is a
# single, reviewable edit.
CANONICAL_HEADERS: dict[bytes, bytes] = {
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"strict-origin-when-cross-origin",
    b"x-frame-options": b"SAMEORIGIN",
    b"x-permitted-cross-domain-policies": b"none",
    b"x-xss-protection": b"1; mode=block",
}

# Values that are safe to keep when a handler deliberately sets a stronger
# policy than our default. Anything else is replaced.
_REFERRER_SAFE = frozenset(
    {
        b"no-referrer",
        b"no-referrer-when-downgrade",
        b"origin",
        b"origin-when-cross-origin",
        b"same-origin",
        b"strict-origin",
        b"strict-origin-when-cross-origin",
    }
)
_FRAME_SAFE = frozenset({b"sameorigin", b"deny"})


def is_weakened(name: bytes, value: bytes) -> bool:
    """Whether a header value weakens a security decision."""
    key = name.lower()
    lowered = value.strip().lower()
    if key == b"x-content-type-options":
        return lowered != b"nosniff"
    if key == b"referrer-policy":
        return lowered not in _REFERRER_SAFE
    if key == b"x-frame-options":
        return lowered not in _FRAME_SAFE
    if key == b"x-xss-protection":
        # "0" explicitly switches the browser's XSS auditor off, and any
        # non-canonical value is at best weaker than "1; mode=block". Only
        # the enabling block form may be kept from an upstream handler.
        return lowered != b"1; mode=block"
    if key == b"x-permitted-cross-domain-policies":
        # "all" (and every value other than "none") re-enables cross-domain
        # policy files for Flash/PDF, undoing the canonical restriction.
        return lowered != b"none"
    return False


def canonical_security_headers(*, include_frame: bool = True) -> List[Header]:
    """Return the full set of canonical security headers."""
    headers: List[Header] = [
        (b"X-Content-Type-Options", b"nosniff"),
        (b"Referrer-Policy", b"strict-origin-when-cross-origin"),
        (b"X-Permitted-Cross-Domain-Policies", b"none"),
        (b"X-XSS-Protection", b"1; mode=block"),
    ]
    if include_frame:
        headers.append((b"X-Frame-Options", b"SAMEORIGIN"))
    return headers


def merge_security_headers(
    existing: Iterable[Header], *, include_frame: bool = True
) -> List[Header]:
    """Merge canonical security headers into ``existing``.

    Correct/stronger values supplied upstream are preserved. Weakened values
    are replaced with the canonical one, and missing headers are appended.
    ``include_frame=False`` is for media responses that are deliberately
    embeddable; in that case any pre-existing frame header is still corrected
    to a safe value rather than left as ``ALLOWALL``.
    """
    result: List[Header] = list(existing)
    seen: set[bytes] = set()
    merged: List[Header] = []
    for name, value in result:
        key = name.lower()
        if key in CANONICAL_HEADERS and is_weakened(name, value):
            merged.append((name, CANONICAL_HEADERS[key]))
        elif key == b"x-frame-options" and not include_frame:
            # Media is embeddable via CSP frame-ancestors; never keep an
            # explicitly permissive legacy frame header.
            merged.append((name, b"SAMEORIGIN"))
        else:
            merged.append((name, value))
        if key in CANONICAL_HEADERS:
            seen.add(key)

    for name, value in canonical_security_headers(include_frame=include_frame):
        if name.lower() not in seen:
            merged.append((name, value))
    return merged


__all__ = [
    "CANONICAL_HEADERS",
    "canonical_security_headers",
    "is_weakened",
    "merge_security_headers",
]
