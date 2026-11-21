"""
Outbound webhook delivery engine.

The retry queue (:mod:`src.core.webhooks.retry`) stores outbound
deliveries that could not be completed on the first attempt. This module
turns *due* records into real outbound HTTP calls (with the signing
headers captured at enqueue time) and drives the queue to its terminal
state:

* ``2xx`` responses  -> ``mark_success``
* other status codes -> ``mark_failure`` (schedules the next backoff
  attempt, or dead-letters once the attempt budget is exhausted)
* transport errors   -> ``mark_failure`` with no status (same backoff)

Every POST is sent to the IP the SSRF validator resolved (with the real
hostname carried in ``Host`` and, for HTTPS, in the TLS SNI), so a DNS
rebinding between validation and connect cannot redirect a delivery to an
internal host.

``process_due_retries`` is the scheduler entry point; it is safe to run
periodically from any worker (each due record is atomically claimed via
a conditional ``UPDATE ... SET status='delivering'`` guarded by rowcount,
so concurrent workers can never double-deliver the same record).
"""

import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple

import utils.logger as logger
from src.core.webhooks.url_safety import (
    MAX_REDIRECT_LOCATION_LENGTH,
    redact_url,
    resolve_redirect_target,
    validate_delivery_endpoint,
)

try:
    import httpx
except ImportError:  # pragma: no cover - optional runtime dependency
    httpx = None

DEFAULT_INTERVAL_SECONDS = 30

# A redirect is a fresh SSRF boundary. We follow at most this many hops and
# re-run the full URL gate (including DNS resolution and IP pinning) on each
# one; anything beyond the budget is reported as a failed delivery rather
# than followed further.
MAX_REDIRECTS = 3

# Signing headers captured at enqueue time carry a timestamp; receivers
# enforcing the documented replay window reject deliveries signed more
# than ~5 minutes ago. Retries (especially manual dead-letter retries)
# can legitimately exceed that, so a stored signature older than the
# window is refreshed (or stripped) before the HTTP call.
SIGNATURE_STALE_AFTER_MS = 5 * 60 * 1000

# Delivery-time ceilings. Enqueue checks are not enough: a row can be
# persisted by an older version or tampered with after insertion, so the
# payload and every header are bounded again immediately before the POST.
MAX_DELIVERY_PAYLOAD_BYTES = 1024 * 1024
MAX_HEADER_NAME_LENGTH = 128
MAX_HEADER_VALUE_LENGTH = 4096
MAX_BATCH_LIMIT = 1000
MAX_INTERVAL_SECONDS = 86400

# Bounded fan-out delivery. A batch of due rows is POSTed concurrently but
# never by more than this many workers, so one slow endpoint cannot stall
# the whole cycle or open an unbounded number of sockets. Every worker still
# runs the full SSRF/signing gate; only the sequential wall-clock wait and
# the head-of-line blocking are removed.
MAX_DELIVERY_CONCURRENCY = 16
DEFAULT_DELIVERY_CONCURRENCY = 8

# When one event fans out to N webhook endpoints, all N retry rows carry the
# same canonical payload JSON string. Delivery used to re-derive the byte size
# of that payload with ``json.dumps`` once per row (N re-encodes of a single
# derived value). Memoize the derived size by that full stored string: it is
# the canonical ``json.dumps(payload, sort_keys=True)`` produced at enqueue,
# so it encodes *every* input to the serialization. A changed payload yields a
# different key and can never observe a stale size. The cache is bounded so a
# flood of distinct payloads cannot retain unbounded JSON strings.
MAX_PAYLOAD_SIZE_CACHE_ENTRIES = 64
# Keys larger than this cannot represent a deliverable payload anyway (the
# delivery bound below is smaller); skipping them keeps a forged oversized
# record from filling the cache with a huge string.
_MAX_PAYLOAD_SIZE_CACHE_KEY_BYTES = MAX_DELIVERY_PAYLOAD_BYTES * 4


class _PayloadSizeCache:
    """Bounded LRU of ``canonical payload JSON -> compact byte size``."""

    def __init__(self, max_entries: int = MAX_PAYLOAD_SIZE_CACHE_ENTRIES) -> None:
        self._max_entries = max(1, int(max_entries))
        self._entries: "OrderedDict[str, int]" = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> Optional[int]:
        """Return the cached size, or ``None`` on a miss (also counted)."""
        size = self._entries.get(key)
        if size is None:
            self._misses += 1
            return None
        self._hits += 1
        self._entries.move_to_end(key)
        return size

    def put(self, key: str, size: int) -> None:
        self._entries[key] = size
        if len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def stats(self) -> Dict[str, int]:
        """Hit/miss counters and size, for observability and regression tests."""
        return {
            "size": len(self._entries),
            "max_entries": self._max_entries,
            "hits": self._hits,
            "misses": self._misses,
        }


_PAYLOAD_SIZE_CACHE = _PayloadSizeCache()


def _payload_serialized_size(payload: Dict[str, Any], raw_json: Any = None) -> int:
    """Return the byte count of the canonical serialization used for the bound.

    The computation on a miss is byte-for-byte the original
    ``json.dumps(payload, sort_keys=True, separators=(",", ":"))`` call, so the
    security bound is unchanged. ``raw_json`` is only a cache key — never a
    substitute for the computation — and is used only when it is a bounded
    string. Callers without a canonical string (direct ``deliver`` calls)
    simply bypass the cache and always compute, as before.
    """
    key: Optional[str] = None
    if (
        isinstance(raw_json, str)
        and raw_json
        and len(raw_json) <= _MAX_PAYLOAD_SIZE_CACHE_KEY_BYTES
    ):
        key = raw_json
        cached = _PAYLOAD_SIZE_CACHE.get(key)
        if cached is not None:
            return cached

    size = len(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )
    if key is not None:
        _PAYLOAD_SIZE_CACHE.put(key, size)
    return size


# RFC 7230 hop-by-hop headers: these govern the connection itself, never the
# payload, and must not be copied from stored metadata into a new request.
_HOP_BY_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "proxy-connection",
    }
)

# Credentials, session cookies and trusted-proxy routing hints. A webhook
# endpoint is external: forwarding any of these leaks secrets or lets a
# stored header spoof the caller's identity/source.
_SENSITIVE_HEADERS = frozenset(
    {
        "authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-auth",
        "x-auth-token",
        "x-csrf-token",
        "x-xsrf-token",
        "x-plexichat-token",
        "x-webhook-token",
        "x-secret",
        "x-session",
        "x-amz-security-token",
        "x-goog-api-key",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
    }
)

# Transport-owned headers: the delivery engine sets these itself. Letting
# stored metadata override them would allow Host/body-length confusion.
_STRUCTURAL_HEADERS = frozenset(
    {"host", "content-type", "content-length", "accept-encoding", "expect"}
)


def sanitize_forwarded_headers(headers: Dict[str, Any]) -> Dict[str, str]:
    """Return only headers safe to forward to an external webhook endpoint.

    Stored signing headers live in retry metadata that may be stale,
    tampered with, or written by an older version. They must not be able to
    inject credentials, hop-by-hop control headers, proxy-routing hints, or
    override the transport's own ``Host``/``Content-Type``. Header values are
    bounded and checked for control characters at the same point.

    Signing headers (``X-Plexichat-Signature`` / ``X-Plexichat-Timestamp``)
    are deliberately not on any deny list: they are the whole point of the
    delivery.
    """
    deny = set(_HOP_BY_HOP_HEADERS) | _SENSITIVE_HEADERS | _STRUCTURAL_HEADERS
    pairs = list((headers or {}).items())

    # RFC 7230: a sender may list extra hop-by-hop headers in Connection.
    # Honour that list so, e.g., ``Connection: X-Internal`` cannot smuggle
    # ``X-Internal`` through as an end-to-end header.
    for raw_name, raw_value in pairs:
        if str(raw_name).strip().lower() == "connection":
            for token in str(raw_value).split(","):
                token = token.strip().lower()
                if token:
                    deny.add(token)

    cleaned: Dict[str, str] = {}
    for raw_name, raw_value in pairs:
        name = str(raw_name)
        value = str(raw_value)
        lowered = name.strip().lower()
        if lowered in deny:
            logger.warning(f"Stripped unsafe header from webhook delivery: {lowered}")
            continue
        if (
            not name
            or len(name) > MAX_HEADER_NAME_LENGTH
            or len(value) > MAX_HEADER_VALUE_LENGTH
            or any(ch in name or ch in value for ch in ("\r", "\n", "\x00"))
        ):
            logger.warning("Dropping an oversized or malformed webhook delivery header")
            continue
        cleaned[name] = value
    return cleaned


class WebhookDeliveryError(Exception):
    """Raised when an outbound webhook delivery fails at the transport level."""


def pinned_request(
    endpoint_url: str, hostname: str, resolved_ip: str
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Return ``(request_url, headers, extensions)`` for an IP-pinned POST.

    ``URLValidator`` resolves the endpoint hostname once and hands back the
    IP it approved. Posting the original URL would make the HTTP client
    resolve the name *again*, so a DNS-rebinding server can answer with a
    public address at validation time and a loopback/private one at connect
    time -- the exact SSRF the validator exists to stop. The media proxy and
    the link-preview service already connect to the resolved IP for this
    reason; outbound webhooks must too.

    The real hostname still has to travel: RFC 7230 requires it in ``Host``
    (bracketed, with the port only when non-default) so virtual hosts route
    correctly, and HTTPS needs it as the TLS SNI / certificate hostname or
    every certificate check fails against a bare IP.

    Rebuilding the URL from parts also drops any ``user:pass@`` userinfo a
    stored endpoint might carry, so credentials are never re-sent.

    The rebuild itself is shared with the other outbound fetchers; this
    wrapper only turns its ``ValueError`` into the delivery error the retry
    queue understands.
    """
    from src.utils.security import pinned_request_target

    try:
        return pinned_request_target(endpoint_url, hostname, resolved_ip)
    except ValueError as exc:
        raise WebhookDeliveryError(f"endpoint URL is unusable: {exc}") from exc


class WebhookDeliverer:
    """POST webhook payloads to external endpoints with stored signing headers.

    Args:
        client: Optional pre-configured HTTP client (testing seam).
        timeout: Per-request timeout when creating the default client.
        signer: Optional ``callable(webhook_id, payload) -> Dict[str, str]``
            used to re-sign deliveries whose captured signing headers are
            older than the replay window (or missing entirely). Typically
            ``WebhookManager.sign_payload``.
        allow_private_network: Testing seam ONLY for loopback end-to-end
            tests; production must keep the strict SSRF default.
        follow_redirects: Follow 3xx responses, but only after re-validating
            each redirect target with the full SSRF gate and re-pinning the
            connection to the newly approved IP.
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        timeout: float = 10.0,
        signer: Optional[Callable[[int, Dict[str, Any]], Dict[str, str]]] = None,
        *,
        allow_private_network: bool = False,
        follow_redirects: bool = True,
    ):
        self._client = client
        self._session_lock = threading.Lock()
        self._timeout = timeout
        self._signer = signer
        self._allow_private_network = allow_private_network
        self._follow_redirects = follow_redirects

        # SECURITY (SSRF on redirect): every 3xx is handled manually in
        # deliver() so the target is re-validated through the full SSRF gate
        # and the connection re-pinned to the newly approved IP. If the HTTP
        # client follows redirects itself, those 3xx responses never reach
        # that gate -- a public -> internal redirect would be followed by the
        # transport with no validation at all. Disable client-side auto-follow
        # on an INJECTED client too (the testing seam), not just on the client
        # we create in _session(), so the manual gate can never be bypassed by
        # a caller handing us an httpx.Client(follow_redirects=True).
        if self._client is not None and hasattr(self._client, "follow_redirects"):
            try:
                self._client.follow_redirects = False
            except Exception:  # best effort: non-httpx seams may be read-only
                logger.debug(
                    "Could not disable follow_redirects on injected webhook client"
                )

    def _session(self) -> Any:
        """Return the HTTP client used for delivery."""
        if self._client is not None:
            return self._client
        if httpx is None:  # pragma: no cover
            raise WebhookDeliveryError("httpx is not installed")
        with self._session_lock:
            if self._client is None:
                # Explicit rather than implicit: redirects are ours alone.
                self._client = httpx.Client(
                    timeout=self._timeout, follow_redirects=False
                )
            return self._client

    def _effective_signing_headers(
        self, webhook_id: Any, payload: Dict[str, Any], stored: Dict[str, str]
    ) -> Dict[str, str]:
        """Return signing headers fit for THIS delivery attempt.

        Stored headers whose timestamp is older than the replay window are
        replaced with a freshly signed pair when a signer is available, or
        dropped entirely otherwise — a frozen stale timestamp would be
        rejected by receivers as a replay.
        """
        timestamp_header = stored.get("X-Plexichat-Timestamp")
        try:
            signed_at = int(timestamp_header) if timestamp_header else 0
        except (TypeError, ValueError):
            signed_at = 0

        fresh_enough = (
            timestamp_header is not None
            and abs(int(time.time() * 1000) - signed_at) <= SIGNATURE_STALE_AFTER_MS
        )
        if fresh_enough:
            return stored

        if self._signer is not None:
            try:
                refreshed = self._signer(webhook_id, payload) or {}
                if refreshed:
                    return refreshed
            except Exception as exc:  # signing is best-effort; never block delivery
                logger.warning(
                    f"Failed to refresh webhook signature for {webhook_id}: {exc}"
                )
        # No signer / signing failed: deliver unsigned rather than with a
        # permanently-rejected stale signature.
        return {}

    @staticmethod
    def _post(
        session: Any,
        request_url: str,
        payload: Dict[str, Any],
        headers: Dict[str, str],
        extensions: Dict[str, Any],
    ) -> Any:
        """Issue one POST. The extension kwarg is omitted for plain HTTP."""
        if extensions:
            return session.post(
                request_url, json=payload, headers=headers, extensions=extensions
            )
        return session.post(request_url, json=payload, headers=headers)

    @staticmethod
    def _redirect_location(response: Any) -> Optional[str]:
        """Read a bounded ``Location`` header, tolerating minimal test clients."""
        headers = getattr(response, "headers", None)
        if not headers:
            return None
        try:
            location = headers.get("Location")
        except Exception:
            return None
        if not location or len(str(location)) > MAX_REDIRECT_LOCATION_LENGTH:
            return None
        return str(location)

    @staticmethod
    def _build_target(
        url: str,
        hostname: str,
        resolved_ip: str,
        signing_headers: Dict[str, str],
    ) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
        """Build the IP-pinned ``(url, headers, extensions)`` for one hop.

        Connecting to the IP the validator approved prevents DNS rebinding
        between validation and connect. The loopback test seam passes no IP,
        so it keeps posting the literal URL to the local server it bound.
        """
        request_url = url
        extensions: Dict[str, Any] = {}
        headers = {"Content-Type": "application/json"}
        if resolved_ip:
            request_url, pinned_headers, extensions = pinned_request(
                url, hostname, resolved_ip
            )
            headers.update(pinned_headers)
        # Stored headers are untrusted metadata. Strip hop-by-hop/credential
        # headers and bound values before they are merged into the request;
        # this runs for every redirect hop as well as the original target.
        for key, value in sanitize_forwarded_headers(signing_headers).items():
            headers[str(key)] = str(value)
        return request_url, headers, extensions

    def deliver(self, record: Dict[str, Any]) -> int:
        """Deliver one queued record and return the HTTP status code.

        The endpoint is re-validated immediately before the fetch, and every
        redirect hop is validated again (with a fresh DNS resolution and IP
        pin) before it is followed. A redirect to a private/reserved target
        raises :class:`WebhookDeliveryError` without issuing the second POST.

        Raises:
            WebhookDeliveryError: when the record is malformed, the endpoint
                or a redirect target fails SSRF validation, or the HTTP
                transport fails (connection errors, timeouts...).
        """
        endpoint_url = record.get("endpoint_url")
        payload = record.get("payload")
        if not endpoint_url or not isinstance(payload, dict):
            raise WebhookDeliveryError(
                "retry record missing endpoint_url or a dict payload"
            )

        # Bound the payload at delivery time as well: a tampered/stale queue
        # row must not be able to push an arbitrarily large body downstream.
        # The canonical stored string (when present) keys a bounded cache so a
        # fan-out of one event to N endpoints encodes the shared payload once,
        # not N times; the size itself is still derived from ``payload``.
        try:
            payload_size = _payload_serialized_size(
                payload, record.get("payload_raw_json")
            )
        except (TypeError, ValueError) as exc:
            raise WebhookDeliveryError("payload is not JSON serializable") from exc
        if payload_size > MAX_DELIVERY_PAYLOAD_BYTES:
            raise WebhookDeliveryError(
                f"payload exceeds maximum delivery size "
                f"({MAX_DELIVERY_PAYLOAD_BYTES} bytes)"
            )

        # SECURITY (SSRF): re-validate immediately before the fetch. A row
        # persisted by an older version, or mutated after enqueue, must
        # never reach an internal target. Fail closed on validation errors
        # so the record is marked failed/dead-letter instead of delivered.
        try:
            hostname, resolved_ip = validate_delivery_endpoint(
                endpoint_url, allow_private_network=self._allow_private_network
            )
        except ValueError as exc:
            logger.warning(
                f"Refusing webhook delivery to unsafe endpoint "
                f"{redact_url(endpoint_url)}: {exc}"
            )
            raise WebhookDeliveryError(
                "endpoint URL refused by SSRF validation"
            ) from exc

        metadata = record.get("metadata") or {}
        original = metadata.get("original_signing_headers") or {}
        effective = self._effective_signing_headers(
            record.get("webhook_id"), payload, original
        )

        logical_url = endpoint_url  # hostname-bearing URL for redirect resolution
        request_url, headers, extensions = self._build_target(
            endpoint_url, hostname, resolved_ip, effective
        )
        session = self._session()

        # The loop always executes at least once (MAX_REDIRECTS >= 0) and the
        # final hop returns internally, so this is only a defensive default
        # for the post-loop return below.
        status = 0
        for hop in range(MAX_REDIRECTS + 1):
            try:
                response = self._post(
                    session, request_url, payload, headers, extensions
                )
            except Exception as exc:
                # F6: redact credentials/query tokens before the URL hits logs.
                logger.warning(
                    f"Webhook delivery transport error to {redact_url(logical_url)}: {exc}"
                )
                raise WebhookDeliveryError(f"delivery failed: {exc}") from exc

            status = int(response.status_code)
            location = self._redirect_location(response)
            if (
                not self._follow_redirects
                or not (300 <= status < 400)
                or not location
                or hop >= MAX_REDIRECTS
            ):
                return status

            # SECURITY (SSRF): the redirect target is a NEW untrusted URL.
            # Run it through the same gate as the original endpoint -- DNS
            # resolution, private/reserved IP rejection, port/scheme checks
            # -- and pin the next request to the address it approved. A
            # public -> internal redirect can never reach the inner service.
            try:
                redirect_url, next_hostname, next_ip = resolve_redirect_target(
                    logical_url,
                    location,
                    allow_private_network=self._allow_private_network,
                )
            except ValueError as exc:
                logger.warning(
                    f"Refusing webhook redirect to unsafe target "
                    f"{redact_url(location)}: {exc}"
                )
                raise WebhookDeliveryError(
                    "redirect target refused by SSRF validation"
                ) from exc

            logger.debug(
                f"Webhook redirect approved after SSRF re-check: hop {hop + 1}"
            )
            logical_url = redirect_url
            request_url, headers, extensions = self._build_target(
                redirect_url, next_hostname, next_ip, effective
            )

        # Redirect budget exhausted: report the last 3xx as a failed delivery.
        return status


def _deliver_one(deliverer: Any, record: Dict[str, Any]) -> Tuple[str, Any]:
    """Normalize one delivery into ``(kind, value)`` without raising."""
    try:
        return ("status", deliverer.deliver(record))
    except WebhookDeliveryError as exc:
        return ("transport", str(exc))
    except Exception as exc:  # defensive: a deliverer bug must not break the loop
        logger.error(f"Unexpected webhook delivery error: {exc}")
        return ("error", str(exc))


def _mark_failed(
    retry_queue: Any,
    record: Dict[str, Any],
    response_status: Optional[int],
    error: Optional[str],
) -> None:
    """Resolve a failed delivery without re-reading counters we already own.

    ``retry_queue`` is normally a ``WebhookRetryQueue``; its fast path reuses
    the ``attempt_count``/``max_attempts`` already loaded by ``SELECT *`` when
    the record was claimed. Wrappers and injected test doubles that only
    implement the original three-argument ``mark_failure`` keep working via the
    fallback, so the queue interface is unchanged.
    """
    fast = getattr(retry_queue, "mark_failure_with_counts", None)
    if callable(fast):
        fast(record, response_status, error)
    else:
        retry_queue.mark_failure(record["id"], response_status, error)


def _apply_outcome(
    retry_queue: Any,
    stats: Dict[str, int],
    record: Dict[str, Any],
    outcome: Tuple[str, Any],
) -> None:
    """Resolve one (already delivered) record in the queue, single-threaded."""
    stats["processed"] += 1
    kind, value = outcome
    if kind == "status":
        if 200 <= value < 300:
            retry_queue.mark_success(record["id"], value)
            stats["delivered"] += 1
        else:
            _mark_failed(retry_queue, record, value, f"HTTP {value}")
            stats["failed"] += 1
    elif kind == "transport":
        stats["transport_errors"] += 1
        stats["failed"] += 1
        _mark_failed(retry_queue, record, None, value)
    else:
        stats["transport_errors"] += 1
        stats["failed"] += 1
        _mark_failed(retry_queue, record, None, "delivery error")


def process_due_retries(
    retry_queue: Any,
    deliverer: Optional[WebhookDeliverer] = None,
    limit: int = 100,
    *,
    max_concurrency: int = 1,
) -> Dict[str, int]:
    """Deliver all due retry records and update the queue.

    Args:
        retry_queue: A ``WebhookRetryQueue`` instance.
        deliverer: ``WebhookDeliverer`` (or any object with a ``deliver``
            method returning the HTTP status). Defaults to a real
            httpx-based deliverer.
        limit: Maximum number of due records to process in this pass.
        max_concurrency: Upper bound on simultaneous HTTP deliveries. The
            default of 1 keeps the original strictly sequential behaviour;
            the scheduled worker opts in to a bounded pool. Queue updates
            always run on the calling thread in ``due`` order, so DB writes
            keep their original order and no queue state is shared. Every
            delivery still executes the full SSRF and signing gates.

    Returns:
        Summary dict with ``processed``, ``delivered``, ``failed`` and
        ``transport_errors`` counts.
    """
    if deliverer is None:
        deliverer = WebhookDeliverer()

    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, MAX_BATCH_LIMIT))
    try:
        max_concurrency = int(max_concurrency)
    except (TypeError, ValueError):
        max_concurrency = 1
    max_concurrency = max(1, min(max_concurrency, MAX_DELIVERY_CONCURRENCY))

    due = retry_queue.get_pending_retries(limit=limit)
    stats = {"processed": 0, "delivered": 0, "failed": 0, "transport_errors": 0}
    if not due:
        return stats

    if max_concurrency == 1 or len(due) == 1:
        for record in due:
            _apply_outcome(retry_queue, stats, record, _deliver_one(deliverer, record))
        return stats

    # Bounded parallel POSTs: claims were already made atomically by
    # get_pending_retries, so no two workers can deliver the same row. Results
    # are indexed by position and applied in order on this thread only.
    outcomes: List[Any] = [None] * len(due)
    with ThreadPoolExecutor(
        max_workers=min(max_concurrency, len(due)),
        thread_name_prefix="webhook-delivery",
    ) as pool:
        futures = {
            pool.submit(_deliver_one, deliverer, record): i
            for i, record in enumerate(due)
        }
        for future in as_completed(futures):
            outcomes[futures[future]] = future.result()

    for record, outcome in zip(due, outcomes):
        _apply_outcome(retry_queue, stats, record, outcome)
    return stats


class WebhookDeliveryJob:
    """Scheduled background worker that drains the webhook retry queue.

    Mirrors the ``StashCleanupJob`` / ``RetentionCleanupJob`` pattern
    (daemon thread loop): every ``webhooks.delivery_interval_seconds`` it
    calls :func:`process_due_retries` to deliver due records. The interval
    is config-driven and <= 0 disables the worker; failures inside a cycle
    are isolated so one bad delivery never aborts the loop.
    """

    def __init__(self, db: Any, config: Optional[Dict[str, Any]] = None) -> None:
        self._db = db
        self._config = config
        self._is_running = False
        self._thread: Optional[threading.Thread] = None

    def _interval_seconds(self) -> Optional[int]:
        webhook_cfg = self._config or {}
        interval = webhook_cfg.get(
            "delivery_interval_seconds", DEFAULT_INTERVAL_SECONDS
        )
        try:
            interval = int(interval)
        except (TypeError, ValueError):
            interval = DEFAULT_INTERVAL_SECONDS
        if interval <= 0:
            return None
        # Cap an operator-supplied interval so a typo cannot stall the
        # delivery worker (and its retry backlog) for effectively forever.
        return min(interval, MAX_INTERVAL_SECONDS)

    def _concurrency(self) -> int:
        webhook_cfg = self._config or {}
        raw = webhook_cfg.get("delivery_concurrency", DEFAULT_DELIVERY_CONCURRENCY)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            n = DEFAULT_DELIVERY_CONCURRENCY
        return max(1, min(n, MAX_DELIVERY_CONCURRENCY))

    def start(self) -> None:
        """Start the background delivery loop."""
        if self._is_running:
            return
        if self._db is None:
            logger.info("Webhook delivery job skipped: no database")
            return
        if self._interval_seconds() is None:
            logger.info(
                "Webhook delivery job disabled (delivery_interval_seconds <= 0)"
            )
            return

        self._is_running = True
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="WebhookDeliveryJob"
        )
        self._thread.start()
        logger.info("Webhook delivery job started")

    def _run_forever(self) -> None:
        interval = self._interval_seconds()
        if interval is None:  # start() guarded this; defensive for pyright
            return
        while self._is_running:
            try:
                self.run_once()
            except Exception as exc:  # pragma: no cover - defensive
                logger.error(f"Webhook delivery cycle failed: {exc}")
            # Sleep in small increments so stop() can shut down promptly.
            slept = 0
            step = 1.0
            while self._is_running and slept < interval:
                time.sleep(min(step, interval - slept))
                slept += step

    def run_once(self) -> Dict[str, int]:
        """Run one delivery cycle; returns delivery stats."""
        from src.core.webhooks.retry import WebhookRetryQueue

        retry_queue = WebhookRetryQueue(self._db)
        # Re-sign stale/missing delivery signatures with a FRESH timestamp
        # so retried deliveries (auto backoff past the 5-minute replay
        # window, or manual dead-letter retries) are not rejected as
        # replays by receivers. Unavailable manager -> unsigned fallback.
        signer = None
        try:
            from src.core.webhooks import _get_manager

            signer = _get_manager().sign_payload
        except Exception:
            signer = None
        stats = process_due_retries(
            retry_queue,
            WebhookDeliverer(signer=signer),
            max_concurrency=self._concurrency(),
        )
        # Expired-record cleanup piggybacks on the delivery cycle (the
        # queue self-throttles via CLEANUP_INTERVAL_MS) so delivered/
        # dead-letter rows and stale 'delivering' claims are reaped
        # without a dedicated scheduler entry point.
        try:
            cleaned = retry_queue.cleanup_expired()
            if cleaned:
                stats["cleaned"] = cleaned
        except Exception as exc:  # cleanup must never break delivery
            logger.debug(f"Webhook retry cleanup failed: {exc}")
        if stats["processed"]:
            logger.info(
                f"Webhook delivery cycle: {stats['processed']} due, "
                f"{stats['delivered']} delivered, "
                f"{stats['failed']} failed"
            )
        return stats

    def stop(self) -> None:
        """Stop the background delivery loop."""
        self._is_running = False
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None
