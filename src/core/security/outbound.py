"""The single gate every attacker-controlled outbound URL passes through.

``URLValidator`` already did the IP work (scheme allow-list, DNS resolution,
private/loopback/link-local/CGNAT blocking) but three call sites wrapped it in
their own copy of the same try/except: profile social links, the server icon
URL, and the media hash-report attachment URL. Each copy logged a different
message, and one of them had already drifted to a different scheme check.

``resolve_outbound_url`` is that wrapper, once. Anything that stores or fetches
a user-supplied URL calls it and gets back a value that has been resolved; the
module then refuses to hand the raw string onwards, so DNS rebinding protection
stays meaningful.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

import utils.logger as logger
from src.utils.security import URLValidator

from .errors import OutboundUrlRejected
from .hardening.errors import HardeningViolation
from .hardening.text import reject_control_characters

ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https"})


@dataclass(frozen=True)
class OutboundUrl:
    """A URL that has passed the SSRF gate.

    ``hostname`` and ``address`` are the pair the connection must use: resolve
    once, connect to ``address``, send ``hostname`` as the Host header. Reusing
    the name for the connection is what DNS rebinding exploits.
    """

    url: str
    hostname: str
    address: str


def parse_outbound_url(value: str, field: str = "url") -> OutboundUrl:
    """Validate ``value`` and return the resolved parts.

    Raises ``OutboundUrlRejected`` (a ``ValueError``) for a non-http(s) scheme
    or an address the server must not reach. The exception message names the
    field; the public detail never contains the resolved IP.
    """
    try:
        # A CR/LF/TAB embedded in a URL is invisible to ``urlparse`` but can
        # split a header in a sink that stores or fetches the URL later.
        candidate = reject_control_characters(value, field).strip()
    except HardeningViolation as exc:
        logger.warning(f"Rejected {field}: control characters in URL")
        raise OutboundUrlRejected(
            f"{field} contains control characters", field=field
        ) from exc
    if not candidate:
        raise OutboundUrlRejected(f"{field} must not be empty", field=field)

    scheme = (urlparse(candidate).scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        logger.warning(f"Rejected {field}: scheme {scheme or 'none'} not allowed")
        raise OutboundUrlRejected(f"{field} must use http or https", field=field)

    try:
        hostname, address = URLValidator().validate_url_for_request(candidate)
    except Exception as exc:
        # Log the reason, never return it: "resolved to 10.0.0.5" in a 400 is a
        # free port scan.
        logger.warning(f"Rejected {field} failing SSRF check: {exc}")
        raise OutboundUrlRejected(
            f"{field} resolved to a forbidden address", field=field
        ) from exc

    return OutboundUrl(url=candidate, hostname=hostname, address=address)


def resolve_outbound_url(value: Optional[str], field: str = "url") -> Optional[str]:
    """``parse_outbound_url`` for optional fields, returning the URL string.

    Kept as a thin function because most call sites only want the validated
    string back to store; callers that connect use ``parse_outbound_url``.
    """
    if not value:
        return value
    return parse_outbound_url(value, field).url
