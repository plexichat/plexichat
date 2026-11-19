"""Auditing and de-duplication for response security headers.

``security_header_policy`` defines the canonical values; this module handles
the two failure modes a stamping middleware still has to deal with:

* an upstream handler (or a reflected value) emits a *weakened* security
  header, and
* duplicate or excessive headers accumulate, which some proxies/clients
  resolve inconsistently and which can be used to smuggle a second value.

The helpers here never drop a correct header; they only remove or replace
values that weaken a browser security decision.
"""

from __future__ import annotations

from typing import Iterable, List, Tuple

from .security_header_policy import CANONICAL_HEADERS, is_weakened

Header = Tuple[bytes, bytes]

MAX_RESPONSE_HEADERS = 200

# Headers the response-policy layer forces to canonical, tamper-resistant
# values. They are normally appended at the END of the header list, so a naive
# "keep the first N" bound would silently drop every one of them as soon as an
# upstream handler emits at least ``limit`` other headers. A response without
# its CSP/HSTS/nosniff/frame headers is exactly the weakened response this
# de-duplication layer exists to prevent, so these names are retained first
# when the list has to be truncated.
_PROTECTED_HEADERS = frozenset(
    {
        b"x-content-type-options",
        b"referrer-policy",
        b"x-frame-options",
        b"x-permitted-cross-domain-policies",
        b"x-xss-protection",
        b"content-security-policy",
        b"strict-transport-security",
        b"permissions-policy",
    }
)


def is_unsafe_header(name: bytes, value: bytes) -> bool:
    """True for a header that actively weakens a security decision."""
    key = name.lower()
    if key in CANONICAL_HEADERS and is_weakened(name, value):
        return True
    # Explicitly permissive framing headers are never safe to emit.
    if key == b"x-frame-options" and value.strip().lower() in (
        b"allowall",
        b"allow-from *",
    ):
        return True
    if key == b"referrer-policy" and value.strip().lower() == b"unsafe-url":
        return True
    return False


def strip_unsafe_headers(headers: Iterable[Header]) -> List[Header]:
    """Remove headers that weaken security decisions."""
    return [
        (name, value) for name, value in headers if not is_unsafe_header(name, value)
    ]


def dedupe_headers(headers: Iterable[Header]) -> List[Header]:
    """Collapse repeated header names, keeping the last value (ASGI convention)."""
    order: List[bytes] = []
    values: dict[bytes, bytes] = {}
    for name, value in headers:
        key = name.lower()
        if key not in values:
            order.append(name)
        values[key] = value
    return [(name, values[name.lower()]) for name in order]


def bound_header_count(
    headers: Iterable[Header], limit: int = MAX_RESPONSE_HEADERS
) -> List[Header]:
    """Keep at most ``limit`` headers, never dropping security-critical ones.

    Unprotected headers are kept in first-occurrence order (the historical
    behaviour). Canonical security headers are reserved a slot ahead of them so
    a handler that emits ``limit`` or more ordinary headers cannot push CSP,
    HSTS, nosniff, frame or referrer policy out of the final response. Original
    relative order of the retained headers is preserved.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("limit must be a positive integer")
    entries = list(headers)
    if len(entries) <= limit:
        return entries

    protected_count = sum(
        1 for name, _ in entries if name.lower() in _PROTECTED_HEADERS
    )
    keep_protected = min(protected_count, limit)
    other_budget = max(0, limit - keep_protected)

    result: List[Header] = []
    kept_protected = 0
    kept_other = 0
    for name, value in entries:
        if name.lower() in _PROTECTED_HEADERS:
            if kept_protected < keep_protected:
                result.append((name, value))
                kept_protected += 1
        elif kept_other < other_budget:
            result.append((name, value))
            kept_other += 1
    return result


def audit_security_headers(headers: Iterable[Header]) -> List[str]:
    """Return stable issue codes for missing/weakened security headers."""
    lowered = {name.lower(): value for name, value in headers}
    issues: List[str] = []
    for name in CANONICAL_HEADERS:
        if name not in lowered:
            issues.append(f"missing:{name.decode('latin-1')}")
        elif is_weakened(name, lowered[name]):
            issues.append(f"weakened:{name.decode('latin-1')}")
    return issues


__all__ = [
    "MAX_RESPONSE_HEADERS",
    "audit_security_headers",
    "bound_header_count",
    "dedupe_headers",
    "is_unsafe_header",
    "strip_unsafe_headers",
]
