"""WebSocket handshake validation against cross-site hijacking.

Browsers attach an ``Origin`` header to WebSocket handshakes, so a malicious
page can open a socket to ``/gateway`` using the victim's ambient credentials.
The gateway authenticates with an explicit ticket in IDENTIFY (not a cookie),
but accepting a cross-site socket still enables connection/quotas abuse and
leaks protocol metadata. This module validates the handshake *before* the ASGI
upgrade is accepted: an ``Origin`` from anywhere other than the configured
allowlist is rejected, and any token supplied on the handshake URL must be a
well-formed ticket. Non-browser clients (no ``Origin``) are unaffected.

Two additional failure modes are guarded here:

* **Fail-closed parsing.** If the handshake metadata cannot be inspected (an
  unexpected headers/query object), the request is rejected instead of being
  silently waved through. A security gate that cannot read its inputs must not
  turn into an allow-all.
* **Handshake floods.** A bounded, per-client-IP sliding window limits how
  many socket upgrades a single peer may attempt per minute, so an anonymous
  flood of ``Origin``-less handshakes cannot exhaust connections/tasks.

The self-test exemption only relaxes the *origin* gate (local harnesses do not
send a browser ``Origin``); token validation always applies. Public-facing
flood limits are likewise bypassed only for authenticated local self-tests.
"""

import threading
import time
from collections import deque
from typing import Any, Optional
from urllib.parse import urlsplit


#: WebSocket close code used to reject a handshake policy violation.
HANDSHAKE_REJECT_CLOSE_CODE = 1008  # policy violation

#: Close code for a client that exceeds the handshake flood limit (try later).
HANDSHAKE_THROTTLE_CLOSE_CODE = 1013  # try again later


class HandshakeRejected(Exception):
    """Raised when a WebSocket handshake fails an origin/token check."""

    def __init__(
        self, reason: str, close_code: int = HANDSHAKE_REJECT_CLOSE_CODE
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.close_code = int(close_code)


def _normalize(origins: Any) -> set:
    """Return a set of comparable origins, ignoring wildcards/empty values."""
    if isinstance(origins, str):
        origins = [origins]
    if not isinstance(origins, (list, tuple, set, frozenset)):
        return set()
    out = set()
    for origin in origins:
        if not isinstance(origin, str):
            continue
        value = origin.strip().rstrip("/")
        if not value or value == "*":
            continue
        out.add(value)
    return out


def allowed_origins() -> set:
    """Load the WebSocket origin allowlist, falling back to API CORS origins.

    An explicit ``websocket.allowed_origins`` setting wins; otherwise the API
    CORS allowlist is reused so operators configure browser origins in one
    place. A wildcard is never treated as an allow-all.
    """
    try:
        import utils.config as config

        ws_conf = config.get("websocket", {}) or {}
        explicit = ws_conf.get("allowed_origins")
        if explicit:
            return _normalize(explicit)
        api_conf = config.get("api", {}) or {}
        return _normalize(api_conf.get("cors_origins"))
    except Exception:
        return set()


def origin_allowed(origin: Optional[str], allowed: Optional[set] = None) -> bool:
    """Whether a browser ``Origin`` may open a gateway socket.

    No origin means a non-browser client and is allowed (the IDENTIFY ticket
    remains the authentication gate). When an origin is present it must be a
    parseable absolute origin that appears in the allowlist; if no allowlist is
    configured the request fails closed.
    """
    if origin is None or origin == "":
        return True
    if not isinstance(origin, str):
        return False
    value = origin.strip().rstrip("/")
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    if not parts.scheme or not parts.netloc:
        return False
    allowed = allowed_origins() if allowed is None else _normalize(allowed)
    if not allowed:
        return False
    return value in allowed


#: Sentinel returned when handshake metadata exists but cannot be read.
_UNINSPECTABLE = object()


def _get_header(headers: Any, name: str) -> Any:
    """Read a header case-insensitively from a mapping or list of pairs.

    Returns the header value, ``None`` when it is genuinely absent, or the
    ``_UNINSPECTABLE`` sentinel when ``headers`` is present but cannot be
    parsed. Callers must treat the sentinel as a rejection, never as "no
    origin" (which would silently open the cross-site-hijacking gate).
    """
    if headers is None:
        return None
    # Mapping-like (dict, Starlette Headers): scan keys case-insensitively so
    # a title-case "Origin" cannot slip past the gate.
    if hasattr(headers, "items"):
        try:
            for key, value in headers.items():
                if isinstance(key, bytes):
                    key = key.decode("latin-1", "replace")
                if isinstance(key, str) and key.lower() == name:
                    return value
            return None
        except Exception:
            return _UNINSPECTABLE
    if hasattr(headers, "get"):
        value = headers.get(name)
        if value is None:
            value = headers.get(name.title())
        if value is None:
            value = headers.get(name.upper())
        return value
    # Some ASGI adapters expose headers as an iterable of (name, value) pairs.
    if isinstance(headers, (list, tuple)):
        wanted = name.lower().encode("latin-1"), name.lower()
        found = None
        try:
            for item in headers:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    return _UNINSPECTABLE
                key, val = item
                if isinstance(key, bytes):
                    key = key.decode("latin-1", "replace")
                if not isinstance(key, str) or not isinstance(val, (str, bytes)):
                    return _UNINSPECTABLE
                if isinstance(val, bytes):
                    val = val.decode("latin-1", "replace")
                if key.lower() in wanted:
                    found = val if found is None else f"{found},{val}"
            return found
        except Exception:
            return _UNINSPECTABLE
    return _UNINSPECTABLE


def _get_query_value(query: Any, name: str) -> Any:
    """Read one query parameter, or ``_UNINSPECTABLE`` if unparseable."""
    if query is None:
        return None
    if hasattr(query, "get"):
        return query.get(name)
    if isinstance(query, (list, tuple)):
        found = None
        try:
            for item in query:
                if not isinstance(item, (list, tuple)) or len(item) != 2:
                    return _UNINSPECTABLE
                key, val = item
                if key == name:
                    found = val if found is None else f"{found},{val}"
            return found
        except Exception:
            return _UNINSPECTABLE
    return _UNINSPECTABLE


# --- Bounded per-IP handshake flood guard ---------------------------------

#: Maximum distinct client IPs tracked before the oldest is evicted. This
#: bounds memory even under a distributed source-IP spoofing attempt.
_MAX_TRACKED_PEERS = 4096
_WINDOW_SECONDS = 60.0
_DEFAULT_HANDSHAKE_LIMIT = 120


def _handshake_limit() -> int:
    """Read the configured per-minute handshake ceiling; default is fail-safe."""
    try:
        import utils.config as config

        ws_conf = config.get("websocket", {}) or {}
        value = ws_conf.get("handshake_rate_limit_per_minute")
        if value is None:
            value = ws_conf.get("rate_limit_per_minute")
        if value is None:
            return _DEFAULT_HANDSHAKE_LIMIT
        limit = int(value)
        return limit if limit > 0 else _DEFAULT_HANDSHAKE_LIMIT
    except Exception:
        return _DEFAULT_HANDSHAKE_LIMIT


class _HandshakeRateLimiter:
    """Thread-safe sliding-window limiter with bounded peer cardinality."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict = {}

    def allow(self, key: str, limit: int, now: float) -> bool:
        if limit <= 0:
            return True
        with self._lock:
            events = self._events.get(key)
            if events is None:
                if len(self._events) >= _MAX_TRACKED_PEERS:
                    # Evict the entry with the oldest most-recent event.
                    oldest_key = min(
                        self._events,
                        key=lambda k: self._events[k][-1] if self._events[k] else 0,
                    )
                    self._events.pop(oldest_key, None)
                events = deque()
                self._events[key] = events
            cutoff = now - _WINDOW_SECONDS
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                return False
            events.append(now)
            return True

    def reset(self) -> None:
        """Drop all tracking state (used by tests and on config reload)."""
        with self._lock:
            self._events.clear()


_handshake_rate_limiter = _HandshakeRateLimiter()


def reset_handshake_state() -> None:
    """Clear flood-tracking state (test hook / configuration reload)."""
    _handshake_rate_limiter.reset()


def _peer_key(websocket: Any) -> str:
    """Best-effort client IP for flood keying; unknown peers share a bucket."""
    client = getattr(websocket, "client", None)
    host = getattr(client, "host", None)
    if isinstance(host, str) and host:
        return host
    return "unknown"


def validate_handshake(websocket: Any, is_selftest: bool = False) -> None:
    """Validate origin, token and flood limits, or raise.

    ``is_selftest`` relaxes only the origin gate for the local, secret-bearing
    self-test harness; token shape is always checked so the exemption cannot
    be used to smuggle malformed tickets. Public flood limits are also skipped
    for self-tests, which are trusted internal traffic. Raises
    :class:`HandshakeRejected` on any
    violation so the caller can reject the upgrade before
    ``websocket.accept()``.
    """
    # Connection-DoS guard: cap socket-upgrade attempts per peer BEFORE any
    # other work. Counting every attempt (including ones later rejected for a
    # bad origin/token) bounds the total CPU spent on handshake parsing.
    # Self-test traffic is internal and exempt from this public ceiling.
    if not is_selftest:
        limit = _handshake_limit()
        if limit > 0 and not _handshake_rate_limiter.allow(
            _peer_key(websocket), limit, time.monotonic()
        ):
            raise HandshakeRejected(
                "Too many gateway handshake attempts", HANDSHAKE_THROTTLE_CLOSE_CODE
            )

    headers = getattr(websocket, "headers", {})
    raw_origin = _get_header(headers, "origin")

    # Origin gate (skipped only for an authenticated local self-test request).
    if not is_selftest:
        if raw_origin is _UNINSPECTABLE:
            raise HandshakeRejected("Handshake headers could not be inspected")
        if raw_origin and not origin_allowed(raw_origin):
            raise HandshakeRejected("Origin not allowed for gateway handshake")

    # Optional handshake token: clients that supply one must present a
    # well-formed ticket. The authoritative verification still happens at
    # IDENTIFY; this only rejects malformed values at the earliest boundary.
    # This check is never skipped, including for self-test connections.
    query = getattr(websocket, "query_params", None)
    raw_token = _get_query_value(query, "token")
    if raw_token is _UNINSPECTABLE:
        raise HandshakeRejected("Handshake query could not be inspected")
    if raw_token:
        from .boundaries import validate_ticket

        try:
            validate_ticket(raw_token, "handshake_token")
        except Exception:
            raise HandshakeRejected("Malformed handshake token") from None


def close_reason(exc: HandshakeRejected) -> str:
    """Human-readable close reason, avoiding leaking config details."""
    return exc.reason


__all__ = [
    "HandshakeRejected",
    "HANDSHAKE_REJECT_CLOSE_CODE",
    "HANDSHAKE_THROTTLE_CLOSE_CODE",
    "allowed_origins",
    "origin_allowed",
    "validate_handshake",
    "close_reason",
    "reset_handshake_state",
]
