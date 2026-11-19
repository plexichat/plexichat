"""
Webhook retry queue - Exponential backoff retry system for webhook deliveries.

Provides reliable webhook delivery with:
- Configurable max retries (default 5)
- Exponential backoff with jitter (1s, 2s, 4s, 8s, 16s)
- Per-webhook retry state tracking
- Dead letter queue for permanently failed deliveries
- Automatic cleanup of expired retry records
"""

import json
import secrets
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.core.base import timestamp_ms, generate_id
from src.core.events.snowflakes import stringify_snowflake_ids
from src.core.webhooks.url_safety import validate_delivery_endpoint


class WebhookRetryQueue:
    """Manages webhook delivery retries with exponential backoff."""

    # Retry configuration
    MAX_RETRIES = 5
    BASE_DELAY_MS = 1000  # 1 second
    MAX_DELAY_MS = 60000  # 1 minute cap
    JITTER_MS = 500  # Random jitter to avoid thundering herd
    RETRY_WINDOW_MS = 24 * 3600 * 1000  # 24 hours - auto-abandon after this
    CLEANUP_INTERVAL_MS = 3600 * 1000  # 1 hour between cleanups
    MAX_PAYLOAD_SIZE = 1024 * 1024  # 1MB max stored payload

    # Hard ceilings on untrusted/tamperable queue parameters. A row written by
    # an older version (or a tampered DB) must not be able to request an
    # unbounded retry budget, a multi-gigabyte exponent, or an unbounded batch.
    MAX_ALLOWED_RETRIES = 20
    MAX_BACKOFF_EXPONENT = 30
    MAX_LIMIT = 1000
    MAX_ENDPOINT_URL_LENGTH = 2048
    MAX_EVENT_TYPE_LENGTH = 128
    MAX_HEADER_NAME_LENGTH = 128
    MAX_HEADER_VALUE_LENGTH = 4096

    def __init__(self, db, webhook_manager=None):
        self._db = db
        self._webhook_manager = webhook_manager
        self._last_cleanup = 0

    def _get_timestamp(self) -> int:
        return timestamp_ms()

    def _generate_id(self) -> int:
        return generate_id()

    def _calculate_delay(self, attempt: int) -> int:
        """
        Calculate exponential backoff delay with jitter.

        Delay = min(base * 2^attempt, max_delay) + random(0, jitter)

        ``attempt`` comes from a persisted row and may be corrupt or hostile;
        clamping it before the exponentiation keeps ``2**attempt`` from
        becoming a CPU/memory denial-of-service vector.
        """
        try:
            exponent = int(attempt)
        except (TypeError, ValueError):
            exponent = 0
        exponent = max(0, min(exponent, self.MAX_BACKOFF_EXPONENT))
        delay = min(
            self.BASE_DELAY_MS * (2**exponent),
            self.MAX_DELAY_MS,
        )
        jitter = secrets.randbelow(self.JITTER_MS + 1)
        return delay + jitter

    def enqueue(
        self,
        webhook_id: int,
        event_type: str,
        payload: Dict[str, Any],
        endpoint_url: str,
        signing_headers: Optional[Dict[str, str]] = None,
        *,
        allow_private_network: bool = False,
    ) -> Dict[str, Any]:
        """
        Enqueue a webhook delivery for retry tracking.

        If the first delivery fails, this record enables automatic retries
        with exponential backoff.

        Args:
            webhook_id: ID of the webhook
            event_type: Type of event (e.g., 'message.created')
            payload: Event payload dict
            endpoint_url: Target URL for delivery. Validated for SSRF
                safety (scheme allowlist, private/reserved IP blocking)
                BEFORE insertion; invalid URLs are rejected outright.
            signing_headers: Optional Ed25519 signing headers
            allow_private_network: Testing seam only (loopback end-to-end
                tests). Production callers must keep the strict default.

        Returns:
            Retry queue record dict

        Raises:
            ValueError: When the payload exceeds the size limit or the
                endpoint URL fails SSRF validation.
        """
        now = self._get_timestamp()

        # Bound the untrusted queue inputs before any work or persistence.
        if (
            not isinstance(endpoint_url, str)
            or not endpoint_url
            or len(endpoint_url) > self.MAX_ENDPOINT_URL_LENGTH
        ):
            raise ValueError("Webhook endpoint URL is missing or too long")
        if (
            not isinstance(event_type, str)
            or not event_type
            or len(event_type) > self.MAX_EVENT_TYPE_LENGTH
        ):
            raise ValueError("Webhook event type is missing or too long")
        if signing_headers:
            for name, value in signing_headers.items():
                if (
                    len(str(name)) > self.MAX_HEADER_NAME_LENGTH
                    or len(str(value)) > self.MAX_HEADER_VALUE_LENGTH
                ):
                    raise ValueError("Webhook signing header is too long")

        # SECURITY (SSRF): never persist an endpoint we would refuse to
        # POST to. Rejection happens here AND again at delivery time.
        validate_delivery_endpoint(
            endpoint_url, allow_private_network=allow_private_network
        )

        # Serialize snowflake ids as decimal strings at the wire boundary.
        # A webhook receiver is an external HTTP consumer (almost always
        # JavaScript), and a JS ``Number`` cannot hold a 64-bit snowflake
        # exactly, so a numeric id would be silently rounded. Normalize
        # BEFORE signing below: the Ed25519/HMAC signature covers the JSON
        # body we actually deliver, so stringifying after signing would
        # make every receiver reject the delivery.
        payload = stringify_snowflake_ids(payload)

        # Validate payload size
        payload_json = json.dumps(payload, sort_keys=True)
        if len(payload_json) > self.MAX_PAYLOAD_SIZE:
            raise ValueError("Webhook payload exceeds maximum size")

        # Store signing headers if provided (they're ephemeral per-request,
        # but we need them for retries since timestamps change). If none
        # were supplied but a webhook manager is wired in, derive the
        # Ed25519 signature here so every outbound delivery is signed.
        metadata = {}
        if not signing_headers and self._webhook_manager is not None:
            try:
                signing_headers = self._webhook_manager.sign_payload(
                    webhook_id, payload
                )
            except Exception as e:  # signing is best-effort; never block delivery
                logger.warning(f"Failed to sign webhook delivery for {webhook_id}: {e}")
                signing_headers = None
        if signing_headers:
            metadata["original_signing_headers"] = signing_headers

        retry_id = self._generate_id()
        next_attempt_at = now  # First attempt is immediate

        self._db.execute(
            """INSERT INTO webhook_retry_queue
               (id, webhook_id, event_type, payload, endpoint_url,
                attempt_count, max_attempts, next_retry_at,
                status, created_at, updated_at, metadata)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?, 'pending', ?, ?, ?)""",
            (
                retry_id,
                webhook_id,
                event_type,
                payload_json,
                endpoint_url,
                self.MAX_RETRIES,
                next_attempt_at,
                now,
                now,
                json.dumps(metadata) if metadata else None,
            ),
        )

        logger.debug(
            f"Enqueued webhook delivery {retry_id} for webhook {webhook_id} "
            f"(event: {event_type})"
        )

        return {
            "id": retry_id,
            "webhook_id": webhook_id,
            "event_type": event_type,
            "endpoint_url": endpoint_url,
            "attempt_count": 0,
            "max_attempts": self.MAX_RETRIES,
            "next_retry_at": next_attempt_at,
            "status": "pending",
            "created_at": now,
        }

    def mark_success(self, retry_id: int, response_status: int) -> None:
        """Mark a webhook delivery as successful."""
        now = self._get_timestamp()
        self._db.execute(
            """UPDATE webhook_retry_queue
               SET status = 'delivered', response_status = ?, attempt_count = attempt_count + 1,
                   delivered_at = ?, updated_at = ?
               WHERE id = ?""",
            (response_status, now, now, retry_id),
        )
        logger.debug(f"Webhook delivery {retry_id} succeeded on delivery")

    def mark_failure(
        self,
        retry_id: int,
        response_status: Optional[int] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Mark a webhook delivery as failed and schedule retry.

        If max retries exceeded, moves to dead letter queue.
        Returns updated retry record.
        """
        now = self._get_timestamp()

        row = self._db.fetch_one(
            "SELECT attempt_count, max_attempts FROM webhook_retry_queue WHERE id = ?",
            (retry_id,),
        )
        if not row:
            raise ValueError(f"Retry record {retry_id} not found")

        # Clamp counters read from the DB: a tampered ``max_attempts`` must
        # not turn a transient failure into an unbounded retry loop.
        try:
            current_attempt = int(row["attempt_count"])
        except (TypeError, ValueError):
            current_attempt = 0
        try:
            max_attempts = int(row["max_attempts"])
        except (TypeError, ValueError):
            max_attempts = self.MAX_RETRIES
        current_attempt = max(0, min(current_attempt, self.MAX_ALLOWED_RETRIES))
        max_attempts = max(1, min(max_attempts, self.MAX_ALLOWED_RETRIES))
        new_attempt = current_attempt + 1

        if new_attempt >= max_attempts:
            # Move to dead letter queue
            self._db.execute(
                """UPDATE webhook_retry_queue
                   SET status = 'dead_letter', response_status = ?, last_error = ?,
                       attempt_count = ?, updated_at = ?
                   WHERE id = ?""",
                (response_status, error, new_attempt, now, retry_id),
            )
            logger.warning(
                f"Webhook delivery {retry_id} moved to dead letter queue "
                f"after {new_attempt} attempts"
            )
            return {"id": retry_id, "status": "dead_letter", "attempts": new_attempt}

        # Schedule next retry with exponential backoff
        delay = self._calculate_delay(new_attempt)
        next_attempt_at = now + delay

        self._db.execute(
            """UPDATE webhook_retry_queue
               SET status = 'retrying', response_status = ?, last_error = ?,
                   attempt_count = ?, next_retry_at = ?, updated_at = ?
               WHERE id = ?""",
            (response_status, error, new_attempt, next_attempt_at, now, retry_id),
        )

        logger.debug(
            f"Webhook delivery {retry_id} failed (attempt {new_attempt}/{max_attempts}), "
            f"next retry in {delay}ms"
        )

        return {
            "id": retry_id,
            "status": "retrying",
            "attempts": new_attempt,
            "next_retry_at": next_attempt_at,
            "delay_ms": delay,
        }

    def get_pending_retries(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get and ATOMICALLY CLAIM all webhook deliveries that are due.

        Each returned row is transitioned ``pending``/``retrying`` ->
        ``delivering`` via a conditional ``UPDATE ... WHERE status IN
        ('pending', 'retrying')`` guarded by rowcount, so concurrent
        workers can never pick up (and double-deliver) the same record.
        Callers must resolve every claimed record through
        ``mark_success`` / ``mark_failure``; records whose worker dies
        mid-delivery are re-claimed by :meth:`cleanup_expired` once the
        24h window lapses.
        """
        now = self._get_timestamp()
        rows = self._db.fetch_all(
            """SELECT * FROM webhook_retry_queue
               WHERE status IN ('pending', 'retrying')
                 AND next_retry_at <= ?
               ORDER BY next_retry_at ASC
               LIMIT ?""",
            (now, limit),
        )
        results = []
        for row in rows:
            data = dict(row)
            # Atomic claim: only THIS worker flips the row to
            # 'delivering'; a racing worker's UPDATE matches zero rows.
            cursor = self._db.execute(
                """UPDATE webhook_retry_queue
                   SET status = 'delivering', updated_at = ?
                   WHERE id = ? AND status IN ('pending', 'retrying')""",
                (now, data["id"]),
            )
            if int(getattr(cursor, "rowcount", 0) or 0) != 1:
                continue

            if data.get("payload") and isinstance(data["payload"], str):
                # Keep the stored canonical JSON alongside the parsed dict.
                # Every row fanning out one event carries the same string, so
                # the delivery engine uses it as a cache key and derives the
                # payload byte size once instead of re-encoding per row.
                raw_payload_json = data["payload"]
                try:
                    data["payload"] = json.loads(raw_payload_json)
                    data["payload_raw_json"] = raw_payload_json
                except (json.JSONDecodeError, TypeError):
                    data["payload"] = {}
            if data.get("metadata") and isinstance(data["metadata"], str):
                try:
                    data["metadata"] = json.loads(data["metadata"])
                except (json.JSONDecodeError, TypeError):
                    data["metadata"] = {}
            results.append(data)
        return results

    def get_dead_letter_queue(
        self, webhook_id: Optional[int] = None, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get permanently failed webhook deliveries."""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        limit = max(1, min(limit, self.MAX_LIMIT))
        if webhook_id:
            rows = self._db.fetch_all(
                """SELECT * FROM webhook_retry_queue
                   WHERE status = 'dead_letter' AND webhook_id = ?
                   ORDER BY updated_at DESC LIMIT ?""",
                (webhook_id, limit),
            )
        else:
            rows = self._db.fetch_all(
                """SELECT * FROM webhook_retry_queue
                   WHERE status = 'dead_letter'
                   ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            )
        return [dict(row) for row in rows]

    def retry_dead_letter(self, retry_id: int) -> bool:
        """Manually retry a dead-letter entry.

        Return ``True`` only when an eligible dead-letter row was changed;
        callers must not report success for an unknown or already-retried id.

        The frozen signing headers captured at enqueue time are stripped:
        their timestamp predates the 5-minute replay window receivers
        enforce, so re-using them would guarantee permanent rejection.
        The delivery worker re-signs with a fresh timestamp when a webhook
        manager is available (see ``delivery.WebhookDeliverer``).
        """
        now = self._get_timestamp()
        row = self._db.fetch_one(
            "SELECT metadata FROM webhook_retry_queue WHERE id = ? AND status = 'dead_letter'",
            (retry_id,),
        )
        if not row:
            logger.debug("Dead letter entry %s was not eligible for retry", retry_id)
            return False

        metadata: Dict[str, Any] = {}
        raw_metadata = row["metadata"]
        if raw_metadata:
            if isinstance(raw_metadata, str):
                try:
                    metadata = json.loads(raw_metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            elif isinstance(raw_metadata, dict):
                metadata = dict(raw_metadata)
        metadata.pop("original_signing_headers", None)

        result = self._db.execute(
            """UPDATE webhook_retry_queue
               SET status = 'retrying', attempt_count = 0,
                   next_retry_at = ?, updated_at = ?, metadata = ?
               WHERE id = ? AND status = 'dead_letter'""",
            (
                now,
                now,
                json.dumps(metadata) if metadata else None,
                retry_id,
            ),
        )
        changed = int(getattr(result, "rowcount", 0) or 0)
        if changed == 1:
            logger.info(f"Manually retried dead letter entry {retry_id}")
            return True
        logger.debug("Dead letter entry %s was not eligible for retry", retry_id)
        return False

    def cleanup_expired(self) -> int:
        """
        Clean up old retry records that have been resolved or expired.

        Returns the number of records cleaned up.
        """
        now = self._get_timestamp()

        # Only cleanup periodically
        if now - self._last_cleanup < self.CLEANUP_INTERVAL_MS:
            return 0
        self._last_cleanup = now

        # Delete old delivered/failed records past the window
        cutoff = now - self.RETRY_WINDOW_MS
        delete_cursor = self._db.execute(
            """DELETE FROM webhook_retry_queue
               WHERE status IN ('delivered', 'dead_letter')
                 AND updated_at < ?""",
            (cutoff,),
        )
        deleted = int(getattr(delete_cursor, "rowcount", 0) or 0)

        # Auto-abandon stuck retrying records
        abandon_cursor = self._db.execute(
            """UPDATE webhook_retry_queue
               SET status = 'dead_letter', updated_at = ?
               WHERE status = 'retrying'
                 AND created_at < ?""",
            (now, cutoff),
        )
        abandoned = int(getattr(abandon_cursor, "rowcount", 0) or 0)

        # Re-claim records whose worker died mid-delivery: anything still
        # 'delivering' past the retry window goes back to 'pending' so a
        # live worker can retry it instead of leaking the row forever.
        reclaim_cursor = self._db.execute(
            """UPDATE webhook_retry_queue
               SET status = 'pending', updated_at = ?
               WHERE status = 'delivering'
                 AND updated_at < ?""",
            (now, cutoff),
        )
        reclaimed = int(getattr(reclaim_cursor, "rowcount", 0) or 0)
        if reclaimed:
            logger.info(f"Reclaimed {reclaimed} stale delivering webhook retries")

        logger.debug("Cleaned up expired webhook retry records")
        return deleted + abandoned + reclaimed

    def get_stats(self, webhook_id: Optional[int] = None) -> Dict[str, int]:
        """Get retry queue statistics."""
        base_query = "SELECT status, COUNT(*) as count FROM webhook_retry_queue"
        if webhook_id:
            base_query += " WHERE webhook_id = ?"
        base_query += " GROUP BY status"

        params = (webhook_id,) if webhook_id else ()
        rows = self._db.fetch_all(base_query, params)

        stats = {
            "pending": 0,
            "retrying": 0,
            "delivering": 0,
            "delivered": 0,
            "dead_letter": 0,
            "total": 0,
        }
        for row in rows:
            s = row["status"]
            c = row["count"]
            if s in stats:
                stats[s] = c
            stats["total"] += c

        return stats
