"""Defense-in-depth validation for values the server stores verbatim.

The HTTP schemas validate hostile text and URLs before a request reaches the
manager, but the manager is a public Python API in its own right: fixtures,
tests, the gateway and internal callers invoke ``create_server`` /
``update_server`` directly. Keeping the same invariants at the core boundary
means a caller that bypasses FastAPI cannot persist an SSRF vector or a
control-character-laden name.

The helpers import (never redefine) the shared hardening rules so there is one
scheme allow-list, one SSRF gate and one control-character policy in the code
base.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlparse

from src.core.security.hardening.urls import require_http_url
from src.core.security.outbound import parse_outbound_url

#: Longest description accepted at the core boundary. Matches the HTTP schema
#: so an internal caller cannot persist a description the API would reject.
MAX_SERVER_DESCRIPTION_LENGTH = 1000


def validate_server_media_url(
    value: Optional[str], field: str = "server media URL"
) -> Optional[str]:
    """Return a server icon/banner URL that is safe to persist verbatim.

    Uploaded icons are stored as same-origin paths (``/api/v1/avatars/...``).
    Those are safe by construction: the browser resolves them against our own
    origin and the server never fetches them. Everything else must be an
    absolute HTTP(S) URL that clears both the scheme allow-list and the shared
    SSRF gate (which resolves the host once and refuses private, loopback,
    link-local and otherwise forbidden destinations).

    ``HardeningViolation``/``OutboundUrlRejected`` are both ``ValueError``
    subclasses, so existing handlers keep treating a rejected value as a bad
    request.
    """
    if value is None:
        return None
    url = str(value).strip()
    if not url:
        return None

    parsed = urlparse(url)
    # Same-origin absolute path: no scheme, no authority, no embedded
    # credentials, a single leading slash and no backslash tricks.
    if (
        url.startswith("/")
        and not url.startswith("//")
        and "\\" not in url
        and not parsed.scheme
        and not parsed.netloc
        and not parsed.username
        and not parsed.password
    ):
        return url

    # Remote URLs: enforce the scheme allow-list first (cheap, no DNS), then
    # the SSRF gate. parse_outbound_url re-checks the scheme itself, so the two
    # checks cannot drift silently.
    require_http_url(url, field)
    parse_outbound_url(url, field)
    return url
