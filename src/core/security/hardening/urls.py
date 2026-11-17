"""Rules for URLs that come from configuration or a provider response.

The SSRF gate in :mod:`src.core.security.outbound` resolves and pins addresses
for URLs the server *fetches on a user's behalf*. These helpers cover the other
case: endpoints the operator configures or a provider hands back, where the
question is only whether the URL is the shape it claims to be.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .errors import HardeningViolation


def _require_absolute(value: Any, field: str, schemes: tuple[str, ...]) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() not in schemes or not parsed.hostname:
        raise HardeningViolation(f"{field} must be an absolute {'/'.join(schemes)} URL")
    if parsed.username or parsed.password:
        raise HardeningViolation(f"{field} must not embed credentials")
    return url


def require_https_url(value: Any, field: str = "url") -> str:
    """Return ``value`` if it is an absolute HTTPS URL without credentials."""
    return _require_absolute(value, field, ("https",))


def require_http_url(value: Any, field: str = "url") -> str:
    """Return ``value`` if it is an absolute HTTP(S) URL without credentials."""
    return _require_absolute(value, field, ("http", "https"))


def require_ws_url(value: Any, field: str = "url") -> str:
    """Return ``value`` if it is an absolute WS(S) URL without credentials.

    Voice/signaling endpoints are WebSocket URLs, not HTTP URLs. They carry the
    same injection risk (javascript:/data:/credential smuggling) so apply the
    identical absolute-scheme and no-credentials checks with the ws/wss schemes.
    """
    return _require_absolute(value, field, ("ws", "wss"))
