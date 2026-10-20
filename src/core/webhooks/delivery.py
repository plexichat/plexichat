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

import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple
from urllib.parse import urlparse, urlunparse

import utils.logger as logger
from src.core.webhooks.url_safety import redact_url, validate_delivery_endpoint

try:
    import httpx
except ImportError:  # pragma: no cover - optional runtime dependency
    httpx = None

DEFAULT_INTERVAL_SECONDS = 30

# Signing headers captured at enqueue time carry a timestamp; receivers
# enforcing the documented replay window reject deliveries signed more
# than ~5 minutes ago. Retries (especially manual dead-letter retries)
# can legitimately exceed that, so a stored signature older than the
# window is refreshed (or stripped) before the HTTP call.
SIGNATURE_STALE_AFTER_MS = 5 * 60 * 1000


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
    """
    from src.utils.security import bracket_ip_literal

    parsed = urlparse(endpoint_url)
    default_port = 80 if parsed.scheme == "http" else 443
    try:
        port = parsed.port or default_port
    except ValueError as exc:
        raise WebhookDeliveryError(f"endpoint URL has an invalid port: {exc}") from exc

    request_url = urlunparse(
        (
            parsed.scheme,
            f"{bracket_ip_literal(resolved_ip)}:{port}",
            parsed.path or "/",
            parsed.params,
            parsed.query,
            "",
        )
    )

    host_header = bracket_ip_literal(hostname)
    if port != default_port:
        host_header = f"{host_header}:{port}"

    headers = {"Host": host_header}
    extensions: Dict[str, Any] = {}
    if parsed.scheme == "https":
        extensions["sni_hostname"] = hostname
    return request_url, headers, extensions


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
    """

    def __init__(
        self,
        client: Optional[Any] = None,
        timeout: float = 10.0,
        signer: Optional[Callable[[int, Dict[str, Any]], Dict[str, str]]] = None,
        *,
        allow_private_network: bool = False,
    ):
        self._client = client
        self._timeout = timeout
        self._signer = signer
        self._allow_private_network = allow_private_network

    def _session(self) -> Any:
        """Return the HTTP client used for delivery."""
        if self._client is not None:
            return self._client
        if httpx is None:  # pragma: no cover
            raise WebhookDeliveryError("httpx is not installed")
        return httpx.Client(timeout=self._timeout)

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

    def deliver(self, record: Dict[str, Any]) -> int:
        """Deliver one queued record and return the HTTP status code.

        Raises:
            WebhookDeliveryError: when the record is malformed, the
                endpoint URL fails SSRF validation, or the HTTP transport
                fails (connection errors, timeouts...).
        """
        endpoint_url = record.get("endpoint_url")
        payload = record.get("payload")
        if not endpoint_url or not isinstance(payload, dict):
            raise WebhookDeliveryError(
                "retry record missing endpoint_url or a dict payload"
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

        # Connect to the IP the validator approved: re-resolving the name
        # here would reopen the DNS-rebinding window the check closes. The
        # loopback test seam passes no IP, so it keeps posting the literal
        # URL to the local server it bound.
        request_url = endpoint_url
        extensions: Dict[str, Any] = {}
        headers = {"Content-Type": "application/json"}
        if resolved_ip:
            request_url, pinned_headers, extensions = pinned_request(
                endpoint_url, hostname, resolved_ip
            )
            headers.update(pinned_headers)

        metadata = record.get("metadata") or {}
        original = metadata.get("original_signing_headers") or {}
        effective = self._effective_signing_headers(
            record.get("webhook_id"), payload, original
        )
        for key, value in effective.items():
            headers[str(key)] = str(value)

        session = self._session()
        try:
            if extensions:
                response = session.post(
                    request_url,
                    json=payload,
                    headers=headers,
                    extensions=extensions,
                )
            else:
                response = session.post(request_url, json=payload, headers=headers)
        except Exception as exc:
            # F6: redact credentials/query tokens before the URL hits logs.
            logger.warning(
                f"Webhook delivery transport error to {redact_url(endpoint_url)}: {exc}"
            )
            raise WebhookDeliveryError(f"delivery failed: {exc}") from exc
        return int(response.status_code)


def process_due_retries(
    retry_queue: Any,
    deliverer: Optional[WebhookDeliverer] = None,
    limit: int = 100,
) -> Dict[str, int]:
    """Deliver all due retry records and update the queue.

    Args:
        retry_queue: A ``WebhookRetryQueue`` instance.
        deliverer: ``WebhookDeliverer`` (or any object with a ``deliver``
            method returning the HTTP status). Defaults to a real
            httpx-based deliverer.
        limit: Maximum number of due records to process in this pass.

    Returns:
        Summary dict with ``processed``, ``delivered``, ``failed`` and
        ``transport_errors`` counts.
    """
    if deliverer is None:
        deliverer = WebhookDeliverer()

    due = retry_queue.get_pending_retries(limit=limit)
    stats = {"processed": 0, "delivered": 0, "failed": 0, "transport_errors": 0}

    for record in due:
        stats["processed"] += 1
        try:
            status = deliverer.deliver(record)
        except WebhookDeliveryError as exc:
            stats["transport_errors"] += 1
            stats["failed"] += 1
            retry_queue.mark_failure(record["id"], None, str(exc))
            continue
        except Exception as exc:  # defensive: a deliverer bug must not break the loop
            logger.error(f"Unexpected webhook delivery error: {exc}")
            stats["transport_errors"] += 1
            stats["failed"] += 1
            retry_queue.mark_failure(record["id"], None, "delivery error")
            continue

        if 200 <= status < 300:
            retry_queue.mark_success(record["id"], status)
            stats["delivered"] += 1
        else:
            retry_queue.mark_failure(record["id"], status, f"HTTP {status}")
            stats["failed"] += 1

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
        return interval

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
        stats = process_due_retries(retry_queue, WebhookDeliverer(signer=signer))
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
