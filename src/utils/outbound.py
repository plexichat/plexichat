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

import http.client
import ssl
from typing import Any, Dict, Optional
from urllib.error import HTTPError
from urllib.parse import urlparse, urlsplit
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


# --- DNS-rebinding-safe transport -------------------------------------------
#
# ``urlopen`` above enforces the scheme allowlist and refuses redirects, but it
# still hands the hostname back to the resolver at connect time. A caller that
# checked the hostname's DNS answer for private addresses (``URLValidator``)
# is therefore vulnerable to DNS rebinding: the answer can be public for the
# check and loopback/private for the actual connection. Callers that need that
# guarantee use ``pinned_urlopen``: it validates the URL once and connects to
# the *approved* IP, sending the real hostname in ``Host`` (and as TLS SNI) so
# virtual hosting and certificate verification keep working.


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection to an IP, presenting the real hostname as SNI.

    ``HTTPSConnection.connect`` would derive SNI from ``self.host`` (the pinned
    IP here), which breaks certificate verification for every normal TLS site.
    Skip that wrap and apply it with the validator-approved hostname instead.
    Proxy tunnelling keeps its own SNI name, mirroring the stdlib behaviour.
    """

    def __init__(self, host, port, *, sni_hostname: str, **kwargs):
        super().__init__(host, port, **kwargs)
        self._sni_hostname = sni_hostname

    def connect(self) -> None:
        # HTTPConnection.connect opens the TCP socket (to the pinned IP, or to
        # the tunnel proxy when configured) without any TLS wrapping.
        http.client.HTTPConnection.connect(self)
        # Both attributes are set by http.client on the base class but are not
        # part of its public surface, so read them dynamically.
        server_hostname = getattr(self, "_tunnel_host", None) or self._sni_hostname
        self.sock = getattr(self, "_context").wrap_socket(
            self.sock, server_hostname=server_hostname
        )


class _PinnedResponse:
    """Small context-managed facade that closes both response and connection."""

    def __init__(self, conn: http.client.HTTPConnection, response: Any):
        self._conn = conn
        self._response = response

    def __enter__(self) -> "_PinnedResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False

    def read(self, *args, **kwargs) -> bytes:
        return self._response.read(*args, **kwargs)

    @property
    def status(self) -> int:
        return self._response.status

    @property
    def code(self) -> int:
        return self._response.status

    @property
    def headers(self) -> Any:
        return self._response.headers

    def getheader(self, name: str, default: Any = None) -> Any:
        return self._response.getheader(name, default)

    def close(self) -> None:
        self._response.close()
        self._conn.close()


def pinned_urlopen(
    url: str,
    timeout: float = 30,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
) -> _PinnedResponse:
    """Validate ``url`` then connect to the SSRF-approved address.

    The URL is resolved and screened by ``URLValidator`` (scheme allowlist,
    forbidden hosts, private/reserved IPs). The connection goes to the returned
    IP while ``Host`` carries the real hostname and HTTPS uses it for SNI, so
    there is no second DNS lookup that a rebinding server could exploit.
    Redirects are not followed: a redirect would move the request to an
    authority that has not been screened.

    Raises ``ValueError`` for an invalid/forbidden URL and ``HTTPError`` for a
    non-2xx response (matching ``urlopen``'s error contract).
    """
    from src.utils.security import URLValidator, bracket_ip_literal

    hostname, resolved_ip = URLValidator().validate_url_for_request(url)
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        raise ValueError(f"invalid port in URL {url!r}: {exc}") from exc
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    host_header = bracket_ip_literal(hostname)
    if port != default_port:
        host_header = f"{host_header}:{port}"
    request_headers = {k: v for k, v in (headers or {}).items() if k.lower() != "host"}
    request_headers["Host"] = host_header

    if scheme == "https":
        conn: http.client.HTTPConnection = _PinnedHTTPSConnection(
            resolved_ip,
            port,
            sni_hostname=hostname,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        conn = http.client.HTTPConnection(resolved_ip, port, timeout=timeout)

    try:
        conn.request(method, path, body=data, headers=request_headers)
        response = conn.getresponse()
    except Exception:
        conn.close()
        raise
    if response.status >= 400:
        body_error = HTTPError(
            url, response.status, response.reason, response.headers, response
        )
        response.close()
        conn.close()
        raise body_error
    return _PinnedResponse(conn, response)
