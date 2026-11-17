"""Web Push transport (VAPID over the browser push services)."""

import ipaddress
import json
import socket
from typing import Any, Dict, Optional
from urllib.parse import urlparse

import utils.logger as logger
from src.core.security.hardening import HardeningViolation, require_https_url

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
    }
)


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


def _require_public_endpoint(endpoint: str) -> str:
    """Refuse endpoints that point at loopback/private/link-local targets.

    ``require_https_url`` guarantees the scheme and absence of embedded
    credentials, but an attacker who can register a Web Push subscription may
    otherwise point its endpoint at ``127.0.0.1``, ``::1``, ``10.0.0.0/8`` or
    the cloud metadata address ``169.254.169.254``; pywebpush would then POST
    an (encrypted) payload straight into the internal network (SSRF). Reject
    literal IPs in every reserved range plus internal/loopback hostnames.
    Ordinary public DNS names are returned unchanged.
    """
    host = (urlparse(endpoint).hostname or "").lower().rstrip(".")
    if not host:
        raise ValueError("endpoint has no host")
    if host in _BLOCKED_ENDPOINT_HOSTS or host.endswith((".local", ".internal")):
        raise ValueError("endpoint targets an internal host")
    address = _endpoint_address(host)
    if address is None:
        # An ordinary DNS name; scheme/no-credentials were already checked by
        # require_https_url. DNS-based rebinding cannot be fully prevented by
        # pywebpush (it resolves the name itself), but literal/internal targets
        # -- including their obfuscated inet_aton spellings -- are blocked here.
        return endpoint
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise ValueError("endpoint targets a non-public IP address")
    return endpoint


class _NoRedirectSession:
    """A minimal requests-session shim that never follows redirects.

    pywebpush accepts any object exposing ``post`` through its
    ``requests_session`` argument. A subscription endpoint is registered by an
    authenticated user, so a validated *public* URL can still answer with a
    30x whose ``Location`` points at loopback or ``169.254.169.254``; requests
    follows that redirect by default and re-POSTs the payload to the internal
    target, bypassing the literal-IP gate above. Forcing
    ``allow_redirects=False`` means only the endpoint we validated is ever
    contacted. ``requests`` is imported lazily because pywebpush (and thus the
    whole transport) is optional.
    """

    def __init__(self) -> None:
        import requests

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
            endpoint = _require_public_endpoint(endpoint)
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
            # (and its encrypted payload) to an internal address. See
            # :class:`_NoRedirectSession`.
            with _NoRedirectSession() as session:
                webpush(
                    subscription_info=subscription_info,
                    data=payload,
                    vapid_private_key=push_config["vapid_private_key"],
                    vapid_claims={"sub": push_config["vapid_subject"]},
                    content_encoding="aes128gcm",
                    requests_session=session,
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
