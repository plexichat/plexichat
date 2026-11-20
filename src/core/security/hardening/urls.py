"""Rules for URLs that come from configuration or a provider response.

The SSRF gate in :mod:`src.core.security.outbound` resolves and pins addresses
for URLs the server *fetches on a user's behalf*. These helpers cover the other
case: endpoints the operator configures or a provider hands back, where the
question is only whether the URL is the shape it claims to be. Part of that
shape is the authority: a literal backslash there makes ``urlparse`` and the
downstream HTTP client disagree about which host to contact, so it is refused
here just as it is at the outbound SSRF gate.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from .errors import HardeningViolation
from .text import reject_control_characters


def _authority_has_backslash(parsed) -> bool:
    """Return whether the URL authority contains a literal backslash.

    ``urlparse`` keeps a backslash as an ordinary character inside the
    authority, so ``urlparse(url).hostname`` can report the host to the RIGHT
    of an ``@``. Browsers, curl and the HTTP clients used downstream follow the
    WHATWG rule for http(s)/ws(s) and read the backslash as a ``/``, connecting
    to the host on the LEFT instead. That parser differential turns a value
    validated here into an SSRF/open-redirect target for every caller that
    does not add its own host gate.

    Only ``/``, ``?`` and ``#`` end the authority, so a backslash before any of
    them is always in the authority (userinfo, host or port). :attr:`netloc` is
    exactly the part ``urlparse`` uses to derive the hostname, and a literal
    backslash has no legitimate place there. A backslash meant as data must be
    percent-encoded (``%5C``), which puts it in the path where it cannot affect
    host selection. A backslash after the authority is harmless and allowed.
    """
    return "\\" in (parsed.netloc or "")


def _require_absolute(value: Any, field: str, schemes: tuple[str, ...]) -> str:
    # Reject all control characters (C0, C1/NEL and the Unicode line/paragraph
    # separators) before parsing. ``urlparse`` tolerates embedded CR, LF, TAB
    # and U+2028/U+2029, but a client that later fetches the URL or writes it
    # into a header/log line can read a different authority (or inject a
    # header) than the one that was checked here.
    url = reject_control_characters(value, field).strip()
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as exc:
        # A malformed bracketed IPv6 literal (e.g. "https://[::1") makes
        # ``.hostname`` raise a bare ValueError; keep the helper's contract.
        raise HardeningViolation(
            f"{field} must be an absolute {'/'.join(schemes)} URL"
        ) from exc
    if parsed.scheme.lower() not in schemes or not hostname:
        raise HardeningViolation(f"{field} must be an absolute {'/'.join(schemes)} URL")
    # Report the parser differential before the credential rule: for a URL like
    # ``http://169.254.169.254\@public.example/x`` the backslash is parsed as
    # userinfo, so a credential-only check would describe a smuggled address as
    # merely credential-bearing. Refuse the dangerous property itself.
    if _authority_has_backslash(parsed):
        raise HardeningViolation(f"{field} contains a backslash in the URL authority")
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
