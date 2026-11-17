"""Single enforcement point for response security headers and CORS.

``security_header_policy`` holds canonical values and ``security_header_audit``
holds the de-duplication helpers. This module composes them into the policy a
stamping middleware applies to *every* HTTP response:

* weakened security headers are replaced, not merely "added if absent";
* duplicates are collapsed and the header count is bounded;
* HSTS / Permissions-Policy / the path-appropriate CSP are forced to canonical
  values so a handler cannot downgrade a browser security decision; and
* CORS response headers are removed unless the reflected origin is on the
  configured allowlist (a handler must never be able to turn an arbitrary
  ``Origin`` into an allowed cross-origin response).
"""

from __future__ import annotations

from typing import Iterable, List, Sequence

from .security_header_audit import bound_header_count, dedupe_headers
from .security_header_policy import merge_security_headers

Header = tuple[bytes, bytes]

HSTS_VALUE = b"max-age=31536000; includeSubDomains"
PERMISSIONS_POLICY_VALUE = (
    b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"
)

CSP_API = b"default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
CSP_MEDIA = (
    b"default-src 'none'; img-src 'self' data: blob: *; "
    b"media-src 'self' *; frame-ancestors *"
)
CSP_SPA = (
    b"default-src 'self'; "
    b"script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
    b"style-src 'self' 'unsafe-inline'; "
    b"img-src 'self' data: blob:; "
    b"font-src 'self' data:; "
    b"connect-src 'self' ws: wss:; "
    b"frame-ancestors 'self'; "
    b"base-uri 'self'; "
    b"form-action 'self'"
)

_CORS_HEADERS = frozenset(
    {
        b"access-control-allow-origin",
        b"access-control-allow-credentials",
        b"access-control-allow-methods",
        b"access-control-allow-headers",
        b"access-control-expose-headers",
        b"access-control-max-age",
    }
)


def _replace_header(
    headers: Iterable[Header], lower_name: bytes, name: bytes, value: bytes
) -> List[Header]:
    """Replace every occurrence of ``lower_name`` with a single canonical one."""
    result: List[Header] = []
    installed = False
    for existing_name, existing_value in headers:
        if existing_name.lower() == lower_name:
            if not installed:
                result.append((name, value))
                installed = True
            continue
        result.append((existing_name, existing_value))
    if not installed:
        result.append((name, value))
    return result


def _enforce_cors(
    headers: Iterable[Header],
    cors_origins: Sequence[str],
    allow_credentials: bool,
) -> List[Header]:
    """Drop CORS headers whose origin was not explicitly allowlisted."""
    entries = list(headers)
    allowed = {
        origin.rstrip("/")
        for origin in cors_origins
        if isinstance(origin, str) and origin and origin != "*"
    }
    wildcard_without_credentials = "*" in cors_origins and not allow_credentials

    origins = [
        value.strip()
        for name, value in entries
        if name.lower() == b"access-control-allow-origin"
    ]

    # ``allow_credentials`` is a browser security decision, not a hint. A
    # handler that emits ``Access-Control-Allow-Credentials: true`` while the
    # operator disabled credentialed CORS must not be able to re-enable it --
    # the middleware is the last word on every CORS response header.
    credentialed = any(
        name.lower() == b"access-control-allow-credentials"
        and value.strip().lower() == b"true"
        for name, value in entries
    )

    valid = bool(origins)
    for origin in origins:
        text = origin.decode("latin-1")
        if text in allowed:
            continue
        # A wildcard is only safe when no credentials are attached; otherwise
        # any site could make authenticated cross-origin requests. An upstream
        # handler that adds ACAC alongside a reflected origin must never turn
        # a wildcard/no-credentials policy into credentialed access.
        if wildcard_without_credentials and not credentialed:
            continue
        valid = False

    if not allow_credentials:
        # Strip credential grants unconditionally; an allowed origin is not a
        # licence to ignore the configured credential policy.
        entries = [
            (name, value)
            for name, value in entries
            if name.lower() != b"access-control-allow-credentials"
        ]

    if valid:
        return entries
    return [
        (name, value) for name, value in entries if name.lower() not in _CORS_HEADERS
    ]


def enforce_response_policy(
    headers: Iterable[Header],
    *,
    path: str,
    api_prefix: str,
    cors_origins: Sequence[str] = (),
    cors_allow_credentials: bool = True,
) -> List[Header]:
    """Return the final header list for a response on ``path``."""
    prefix = (api_prefix or "/api/v1").rstrip("/") or "/api/v1"
    is_media = path.startswith(f"{prefix}/media/")
    is_help = path.startswith(f"{prefix}/help/")
    is_api = path.startswith(f"{prefix}/") and not is_media and not is_help

    result = dedupe_headers(headers)
    # Media is deliberately embeddable; every other path gets a frame header.
    result = merge_security_headers(result, include_frame=not is_media)

    csp = CSP_MEDIA if is_media else (CSP_API if is_api else CSP_SPA)
    result = _replace_header(
        result, b"content-security-policy", b"Content-Security-Policy", csp
    )
    result = _replace_header(
        result, b"strict-transport-security", b"Strict-Transport-Security", HSTS_VALUE
    )
    result = _replace_header(
        result, b"permissions-policy", b"Permissions-Policy", PERMISSIONS_POLICY_VALUE
    )
    result = _enforce_cors(result, cors_origins, cors_allow_credentials)
    return bound_header_count(result)


__all__ = [
    "CSP_API",
    "CSP_MEDIA",
    "CSP_SPA",
    "HSTS_VALUE",
    "PERMISSIONS_POLICY_VALUE",
    "enforce_response_policy",
]
