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


def _authority_has_backslash(url: str) -> bool:
    """Return whether the URL's authority (userinfo/host/port) contains a ``\\``.

    Only ``/``, ``?`` and ``#`` terminate the authority, so a backslash placed
    before any of them stays inside ``urlparse``'s userinfo and
    ``urlparse(url).hostname`` reports the host to the RIGHT of an ``@``. Other
    consumers disagree: ``requests``/urllib3, curl and browsers following the
    WHATWG URL standard treat a backslash as a forward slash in http(s) URLs and
    therefore connect to the host to the LEFT of it.

    That parser differential is an SSRF bypass. For example,
    ``http://169.254.169.254\\@public.example/x`` is validated against the
    public ``public.example`` but fetched from the link-local metadata address
    by a downstream client. A literal backslash has no legitimate place in a
    URL authority; one that is meant as data must be percent-encoded (``%5C``),
    which puts it in the path where it cannot influence host selection.

    ``urlparse``'s ``netloc`` is exactly the authority it uses to derive the
    hostname the gate validates, so a backslash there marks a real disagreement
    with the downstream HTTP client.
    """
    return "\\" in (urlparse(url).netloc or "")


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

    parsed = urlparse(candidate)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        logger.warning(f"Rejected {field}: scheme {scheme or 'none'} not allowed")
        raise OutboundUrlRejected(f"{field} must use http or https", field=field)

    # This gate is the single choke point for URLs the server persists as
    # outbound targets. Every shape validator on the request path already
    # refuses embedded credentials, so accepting them here would let a caller
    # that goes straight to the gate store a ``user:pass@host`` URL whose
    # secret is then echoed to clients, written to logs and re-sent on fetch.
    # Reject before DNS resolution, like the other cheap structural checks.
    if parsed.username or parsed.password:
        logger.warning(f"Rejected {field}: credentials in URL")
        raise OutboundUrlRejected(f"{field} must not embed credentials", field=field)

    # A backslash in the authority makes urlparse (this gate) and the HTTP
    # client that later fetches the URL disagree about which host to contact.
    # Reject it before DNS resolution so a private/metadata address cannot be
    # smuggled into the userinfo and reached by a re-resolving sink.
    if _authority_has_backslash(candidate):
        logger.warning(f"Rejected {field}: backslash in URL authority")
        raise OutboundUrlRejected(
            f"{field} contains a backslash in the URL authority", field=field
        )

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


def reject_forbidden_ip_literal(host: str, field: str = "url") -> None:
    """Reject an IP literal in a non-global range, before any DNS lookup.

    A resolver only ever sees names, and a numeric host can be handed to one in
    several forms (decimal, hex, shortened), so literal addresses are checked on
    their face here as well as after resolution. A non-literal passes through
    untouched: the caller still runs ``parse_outbound_url`` on it.
    """
    import ipaddress

    candidate = host.strip("[]")
    try:
        addr = ipaddress.ip_address(candidate)
    except ValueError:
        return
    if URLValidator().is_private_ip(str(addr)):
        raise OutboundUrlRejected(f"{field} targets a non-public address", field=field)


def resolve_outbound_url(value: Optional[str], field: str = "url") -> Optional[str]:
    """``parse_outbound_url`` for optional fields, returning the URL string.

    Kept as a thin function because most call sites only want the validated
    string back to store; callers that connect use ``parse_outbound_url``.
    """
    if not value:
        return value
    return parse_outbound_url(value, field).url
