"""Web Push transport (VAPID over the browser push services)."""

import ipaddress
import json
import socket
from typing import Any, Dict, Optional, Tuple, cast
from urllib.parse import urlparse

import utils.logger as logger
from src.core.security.hardening import HardeningViolation, require_https_url
from src.utils.security import URLValidator

from .base import PushManagerBase

# Hostnames that must never be reached through a user-supplied Web Push
# subscription endpoint: loopback names, cloud metadata services, and the
# reserved .local/.internal suffixes all designate infrastructure the server
# must not contact on a caller's behalf.
_BLOCKED_ENDPOINT_HOSTS = frozenset(
    {
        "localhost",
        "localhost.localdomain",
        "local",
        "metadata",
        "metadata.google.internal",
        # Common /etc/hosts aliases for the loopback address.
        "ip6-localhost",
        "ip6-loopback",
        "loopback",
    }
)

# Suffixes whose *every* member designates local infrastructure. RFC 6761
# guarantees any name ending in ``.localhost`` resolves to the loopback
# address, and ``.localdomain`` is the conventional loopback alias; neither
# needs a DNS answer to be an SSRF target. The literal-IP gate below cannot
# help here because these are DNS names (no literal to normalise). An
# authenticated caller registering ``https://anything.localhost/...`` would
# otherwise make pywebpush POST the payload straight back to this host.
_BLOCKED_ENDPOINT_SUFFIXES = (".local", ".internal", ".localhost", ".localdomain")


def _endpoint_address(host: str) -> Optional[Any]:
    """Return an IP address for *host*, including legacy numeric spellings.

    ``ipaddress`` only recognises canonical dotted-quad IPv4 and RFC 4291
    IPv6. The system resolver (used by requests/pywebpush) additionally accepts
    the ``inet_aton(3)`` forms -- decimal (``2130706433``), hexadecimal
    (``0x7f000001``), octal (``0177.0.0.1``) and truncated (``127.1``) -- all of
    which name loopback or another private address. Treating those as ordinary
    DNS names would let a registered subscription POST its payload into the
    internal network, defeating the literal-IP check below. Normalise every
    such spelling through ``socket.inet_aton`` before deciding.

    Returns ``None`` for a genuine DNS name (which the checker then admits,
    subject to the hostname rules); returns an ``IPv4Address``/``IPv6Address``
    for any host the resolver would turn into a literal IP.
    """
    candidate = host.strip("[]")
    try:
        return ipaddress.ip_address(candidate)
    except ValueError:
        pass

    # A mapped IPv6 literal may carry an obfuscated IPv4 tail (e.g.
    # ``::ffff:2130706433``). Normalise the tail too rather than letting it
    # slip through as a "DNS name".
    lowered = candidate.lower()
    if lowered.startswith("::ffff:"):
        try:
            return ipaddress.ip_address(socket.inet_aton(candidate[7:]))
        except (OSError, ValueError):
            pass

    try:
        return ipaddress.ip_address(socket.inet_aton(candidate))
    except (OSError, ValueError):
        return None


def _resolve_public_endpoint(endpoint: str) -> Tuple[str, Optional[str]]:
    """Validate an endpoint and return ``(endpoint, address_to_pin)``.

    ``require_https_url`` guarantees the scheme and absence of embedded
    credentials, but an attacker who can register a Web Push subscription may
    otherwise point its endpoint at ``127.0.0.1``, ``::1``, ``10.0.0.0/8`` or
    the cloud metadata address ``169.254.169.254``; pywebpush would then POST
    an (encrypted) payload straight into the internal network (SSRF). Reject
    literal IPs in every reserved range plus internal/loopback hostnames, and
    resolve ordinary DNS names to reject answers that point into those same
    ranges. Only names that resolve to public addresses are returned.

    For a DNS-name endpoint the *validated* address is returned as
    ``address_to_pin``. Resolving to check safety and then handing pywebpush the
    hostname would let it resolve the name a second time -- a rebinding server
    can answer public here and loopback/private at connect time. Callers must
    therefore pin the transport to the returned address. Literal-IP endpoints
    need no pin (there is no second lookup), so their address is ``None``.
    """
    parsed = urlparse(endpoint)
    # A literal backslash in the authority is a parser differential that lets
    # this whole gate be bypassed. ``urlparse`` treats ``\`` as an ordinary
    # character, so ``https://169.254.169.254\push.example/ep`` is read as the
    # (public-looking) hostname ``169.254.169.254\push.example`` and passes
    # every check below. urllib3/requests -- the client pywebpush uses -- apply
    # WHATWG rules and treat ``\`` as ``/`` in an http(s) URL, so they connect
    # to the address to the LEFT of the backslash (the metadata service). A
    # literal backslash has no legitimate place in a URL authority: one meant
    # as data must be percent-encoded (``%5C``), which keeps it in the path
    # where it cannot influence host selection. Refuse it before any host
    # matching. This mirrors the gate in ``outbound.parse_outbound_url``.
    if "\\" in (parsed.netloc or ""):
        raise ValueError("endpoint URL authority contains a backslash")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("endpoint has no host")
    if host in _BLOCKED_ENDPOINT_HOSTS or host.endswith(_BLOCKED_ENDPOINT_SUFFIXES):
        raise ValueError("endpoint targets an internal host")
    # Use the canonical gate so the literal checks here cover the same ranges
    # as every other outbound-URL boundary (CGNAT, NAT64 and IPv4-mapped IPv6
    # in particular -- ``ipaddress.is_private`` alone misses some of them).
    validator = URLValidator()
    address = _endpoint_address(host)
    if address is None:
        # An ordinary DNS name. scheme/no-credentials were already checked by
        # require_https_url, but a *literal* check alone is not enough: an
        # authenticated caller can register a perfectly public-looking name
        # (e.g. ``attacker.example``) whose A/AAAA records point at
        # 127.0.0.1, 10.0.0.0/8 or the cloud metadata address. pywebpush then
        # resolves the name a second time and POSTs the encrypted payload
        # straight into the internal network. Resolve it here and reject every
        # answer that lands in a non-public range.
        #
        # A resolver failure is not a bypass: the transport would fail to
        # connect to the same name for the same reason, so the endpoint is
        # returned unchanged in that case. A name that answers with a private
        # address, however, can never be registered/used as a push endpoint.
        try:
            answers = socket.getaddrinfo(host, None)
        except (socket.gaierror, OSError):
            # No address to pin: the transport will fail on the same lookup.
            return endpoint, None
        for answer in answers:
            ip_text = str(answer[4][0])
            if validator.is_private_ip(ip_text):
                raise ValueError("endpoint resolves to a non-public IP address")
        # Every answer was checked; pin the transport to one we approved so a
        # second (rebound) lookup by the HTTP client cannot reach a private IP.
        pinned = str(answers[0][4][0]) if answers else None
        return endpoint, pinned
    if validator.is_private_ip(str(address)):
        raise ValueError("endpoint targets a non-public IP address")
    # A literal has no second lookup to rebind; no pin needed.
    return endpoint, None


def _require_public_endpoint(endpoint: str) -> str:
    """Backwards-compatible wrapper: validate and return the endpoint string."""
    return _resolve_public_endpoint(endpoint)[0]


def _make_pinned_session(address: str, hostname: str) -> Any:
    """Return a requests session that connects to ``address``, never re-resolving.

    ``URLValidator`` (and the caller contract in ``src.utils.security``) resolve
    once and require the connection to use that IP. pywebpush, however, POSTs to
    the hostname in ``subscription_info['endpoint']`` and requests would resolve
    it again -- the DNS-rebinding window this adapter closes. The connection
    pool is keyed on the validated IP, while TLS still uses the original host
    for SNI and certificate verification, and requests still sends that host in
    ``Host`` and in the VAPID ``aud`` claim. Environment proxies are disabled:
    a proxy would re-resolve the hostname on our behalf and reopen the window.
    """
    import requests
    from requests.adapters import HTTPAdapter

    class _PinnedHTTPSAdapter(HTTPAdapter):
        def __init__(self, target: str, tls_host: str) -> None:
            self._target = target
            self._tls_host = tls_host
            super().__init__()

        def get_connection_with_tls_context(
            self, request: Any, verify: Any, proxies: Any = None, cert: Any = None
        ) -> Any:
            host_params, pool_kwargs = self.build_connection_pool_key_attributes(
                request, verify, cert
            )
            host_params["host"] = self._target
            pool_kwargs["server_hostname"] = self._tls_host
            pool_kwargs["assert_hostname"] = self._tls_host
            return self.poolmanager.connection_from_host(
                **host_params, pool_kwargs=pool_kwargs
            )

    session = requests.Session()
    session.trust_env = False
    session.mount("https://", _PinnedHTTPSAdapter(address, hostname))
    return session


class _NoRedirectSession:
    """A minimal requests-session shim that never follows redirects.

    pywebpush accepts any object exposing ``post`` through its
    ``requests_session`` argument. A subscription endpoint is registered by an
    authenticated user, so a validated *public* URL can still answer with a
    30x whose ``Location`` points at loopback or ``169.254.169.254``; requests
    follows that redirect by default and re-POSTs the payload to the internal
    target, bypassing the literal-IP gate above. Forcing
    ``allow_redirects=False`` means only the endpoint we validated is ever
    contacted. When ``pinned_ip`` is given, the session also connects to that
    validated address instead of re-resolving the hostname (DNS rebinding).
    ``requests`` is imported lazily because pywebpush (and thus the whole
    transport) is optional.
    """

    def __init__(
        self, pinned_ip: Optional[str] = None, tls_hostname: Optional[str] = None
    ) -> None:
        import requests

        if pinned_ip and tls_hostname:
            self._session = _make_pinned_session(pinned_ip, tls_hostname)
        else:
            self._session = requests.Session()

    def post(self, url: str, **kwargs: Any) -> Any:
        kwargs["allow_redirects"] = False
        return self._session.post(url, **kwargs)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "_NoRedirectSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


class WebPushMixin(PushManagerBase):
    """Deliver to browser push subscriptions with VAPID."""

    def _send_web_push(
        self,
        token_record: Dict[str, Any],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        push_config: Dict[str, Any],
        priority: str = "high",
    ) -> Optional[bool]:
        """Send a Web Push notification using the VAPID protocol.

        For the ``web`` platform the stored ``token`` is the subscription
        endpoint; the P-256 public key (``p256dh``) and auth secret
        (``auth``) are required to encrypt the payload.  Previously web
        subscriptions were silently handed to FCM as if the endpoint were an
        FCM token, so every browser push was dropped.  Here we encrypt and
        deliver the message directly to the browser push service.

        Returns ``True`` on successful delivery, ``False`` when the
        subscription is *permanently* rejected by the push service
        (e.g. 404/410 — the caller should drop the token), and ``None`` when
        delivery was skipped because VAPID is not configured, the subscription
        is incomplete, or a *transient* error occurred (network/DNS/TLS).  A
        transient failure must NOT delete an otherwise-valid subscription.
        """
        if not self._vapid_available:
            logger.debug(
                "VAPID not configured - cannot deliver Web Push to "
                f"{token_record.get('token', '')[:16]}..."
            )
            return None

        p256dh = token_record.get("p256dh")
        auth = token_record.get("auth")
        endpoint = token_record.get("token")
        if not p256dh or not auth or not endpoint:
            logger.debug("Web Push subscription missing p256dh/auth/endpoint")
            return None

        # The stored ``token`` is the subscription endpoint the browser push
        # service handed us. pywebpush POSTs the encrypted payload straight to
        # it, so an endpoint that is relative, uses a non-HTTPS scheme or
        # embeds ``user:pass@`` credentials must never reach the transport.
        # Refuse such rows here (skip, leaving the subscription intact) rather
        # than letting a malformed record turn into an off-origin request.
        try:
            endpoint = require_https_url(endpoint, "Web Push endpoint")
            endpoint, pinned_ip = _resolve_public_endpoint(endpoint)
        except (HardeningViolation, ValueError) as exc:
            logger.warning(f"Refusing Web Push delivery: {exc}")
            return None

        try:
            from pywebpush import WebPushException, webpush
        except ImportError:
            logger.warning("pywebpush not installed - Web Push unavailable")
            return None

        subscription_info = {
            "endpoint": endpoint,
            "keys": {"p256dh": p256dh, "auth": auth},
        }
        payload = json.dumps({"title": title, "body": body, "data": data or {}})
        try:
            # Never follow a redirect from the subscription endpoint: a public
            # URL under the caller's control could otherwise bounce the POST
            # (and its encrypted payload) to an internal address. Connect to
            # the IP the gate validated, with the original host used only for
            # Host/SNI/TLS and the VAPID audience -- a second DNS lookup by the
            # client could otherwise rebind to a private address. See
            # :class:`_NoRedirectSession`.
            tls_hostname = (urlparse(endpoint).hostname or "").lower()
            with _NoRedirectSession(
                pinned_ip=pinned_ip, tls_hostname=tls_hostname
            ) as session:
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=push_config["vapid_private_key"],
                    vapid_claims={"sub": push_config["vapid_subject"]},
                    content_encoding="aes128gcm",
                    # pywebpush annotates this as ``requests.Session`` but only
                    # calls ``.post()``; the shim above is deliberately not a
                    # Session (it must never follow a redirect), so cast keeps
                    # the duck-typed contract explicit instead of lying.
                    requests_session=cast(Any, session),
                )
            logger.debug(f"Web Push sent to {endpoint[:16]}...")
            return True
        except WebPushException as e:
            # 404/410 from the push service means the subscription is gone;
            # the caller treats a False return as "remove this token".
            response = getattr(e, "response", None)
            status = getattr(response, "status_code", None)
            logger.warning(
                f"Web Push failed for {endpoint[:16]}... (status={status}, {e})"
            )
            if status in (404, 410):
                return False
            # Any other Web Push error (401, 429, 500, etc.) is transient and
            # must not invalidate an otherwise-valid subscription.
            return None
        except Exception as e:
            # Transient infrastructure failure (DNS, TLS, connection reset).
            # The subscription is still valid; skipping (None) leaves the
            # token intact instead of deleting it.
            logger.warning(f"Web Push skipped (transient) for {endpoint[:16]}...: {e}")
            return None
