"""Outbound HTTP requests with an explicit scheme allowlist and no redirects.

``urllib.request.urlopen`` dispatches on whatever scheme the URL carries, so a
URL that configuration controls can reach ``file:`` or a custom handler that
``urllib`` installed. Every outbound request in the codebase goes through
:func:`urlopen` here, which refuses anything that is not an absolute http(s)
URL before the opener sees it. Callers keep their own host policy (for example
``URLValidator`` for the search backends); this is the transport-level floor.

Redirects are refused as well. The default ``urllib`` redirect handler accepts
any absolute URL in ``Location`` and carries the original request headers
(including ``Authorization``) to it. A caller that validated the initial URL
with a host policy would still be sent to an unvalidated authority -- a public
``https://search.example/`` returning ``302 Location:
http://169.254.169.254/latest/meta-data/`` -- because only the *first* URL is
checked. None of the callers need redirect following, so the shared opener
replaces that handler with one that raises ``HTTPError`` on every 3xx: the
validated target is the only target the transport will contact.
"""

from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class _NoRedirectHandler(HTTPRedirectHandler):
    """Fail closed on 3xx instead of following the redirect target.

    ``urllib``'s ``HTTPRedirectHandler.redirect_request`` builds a new
    ``Request`` from ``Location`` with the original headers intact and without
    re-running any of the caller's URL checks. Refusing the redirect keeps the
    scheme allowlist and the caller's host policy in force for the whole
    request. Raising ``HTTPError`` (rather than returning ``None``) also stops
    the error processor from surfacing a misleading "success" for a 3xx.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, msg, headers, fp)


# Passing a HTTPRedirectHandler subclass makes build_opener skip its default
# redirect handler; the proxy/http(s)/error handlers are otherwise untouched.
_OPENER = build_opener(_NoRedirectHandler())


def urlopen(request: Any, timeout: float = 30) -> Any:
    """Open ``request`` after proving it is an absolute http(s) URL."""
    url = request.full_url if isinstance(request, Request) else str(request)
    parsed = urlparse(url)
    if parsed.scheme.lower() not in _ALLOWED_SCHEMES or not parsed.netloc:
        raise ValueError(f"refusing to open non-http(s) URL: {url!r}")
    return _OPENER.open(request, timeout=timeout)
