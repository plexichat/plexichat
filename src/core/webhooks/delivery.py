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

``process_due_retries`` is the scheduler entry point; it is safe to run
periodically from any worker (each due record is atomically claimed via
a conditional ``UPDATE ... SET status='delivering'`` guarded by rowcount,
so concurrent workers can never double-deliver the same record).
"""

import threading
import time
from typing import Any, Dict, Optional

import utils.logger as logger

try:
    import httpx
except ImportError:  # pragma: no cover - optional runtime dependency
    httpx = None

DEFAULT_INTERVAL_SECONDS = 30


class WebhookDeliveryError(Exception):
    """Raised when an outbound webhook delivery fails at the transport level."""


class WebhookDeliverer:
    """POST webhook payloads to external endpoints with stored signing headers."""

    def __init__(self, client: Optional[Any] = None, timeout: float = 10.0):
        self._client = client
        self._timeout = timeout

    def _session(self) -> Any:
        """Return the HTTP client used for delivery."""
        if self._client is not None:
            return self._client
        if httpx is None:  # pragma: no cover
            raise WebhookDeliveryError("httpx is not installed")
        return httpx.Client(timeout=self._timeout)

    def deliver(self, record: Dict[str, Any]) -> int:
        """Deliver one queued record and return the HTTP status code.

        Raises:
            WebhookDeliveryError: when the record is malformed or the
                HTTP transport fails (connection errors, timeouts...).
        """
        endpoint_url = record.get("endpoint_url")
        payload = record.get("payload")
        if not endpoint_url or not isinstance(payload, dict):
            raise WebhookDeliveryError(
                "retry record missing endpoint_url or a dict payload"
            )

        headers = {"Content-Type": "application/json"}
        metadata = record.get("metadata") or {}
        original = metadata.get("original_signing_headers") or {}
        for key, value in original.items():
            headers[str(key)] = str(value)

        session = self._session()
        try:
            response = session.post(endpoint_url, json=payload, headers=headers)
        except Exception as exc:
            logger.warning(f"Webhook delivery transport error to {endpoint_url}: {exc}")
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
        stats = process_due_retries(retry_queue, WebhookDeliverer())
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
