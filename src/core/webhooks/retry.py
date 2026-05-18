"""
Webhook retry queue - Exponential backoff retry system for webhook deliveries.

Provides reliable webhook delivery with:
- Configurable max retries (default 5)
- Exponential backoff with jitter (1s, 2s, 4s, 8s, 16s)
- Per-webhook retry state tracking
- Dead letter queue for permanently failed deliveries
- Automatic cleanup of expired retry records
"""

import time
import json
import random
from typing import Optional, List, Dict, Any

import utils.logger as logger
from src.utils.encryption import generate_snowflake_id


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

    def __init__(self, db, webhook_manager=None):
        self._db = db
        self._webhook_manager = webhook_manager
        self._last_cleanup = 0

    def _get_timestamp(self) -> int:
        return int(time.time() * 1000)

    def _generate_id(self) -> int:
        return generate_snowflake_id()

    def _calculate_delay(self, attempt: int) -> int:
        """
        Calculate exponential backoff delay with jitter.

        Delay = min(base * 2^attempt, max_delay) + random(0, jitter)
        """
        delay = min(
            self.BASE_DELAY_MS * (2**attempt),
            self.MAX_DELAY_MS,
        )
        jitter = random.randint(0, self.JITTER_MS)  # nosec B311 - not cryptographic
        return delay + jitter

    def enqueue(
        self,
        webhook_id: int,
        event_type: str,
        payload: Dict[str, Any],
        endpoint_url: str,
        signing_headers: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        """
        Enqueue a webhook delivery for retry tracking.

        If the first delivery fails, this record enables automatic retries
        with exponential backoff.

        Args:
            webhook_id: ID of the webhook
            event_type: Type of event (e.g., 'message.created')
            payload: Event payload dict
            endpoint_url: Target URL for delivery
            signing_headers: Optional Ed25519 signing headers

        Returns:
            Retry queue record dict
        """
        now = self._get_timestamp()

        # Validate payload size
        payload_json = json.dumps(payload, sort_keys=True)
        if len(payload_json) > self.MAX_PAYLOAD_SIZE:
            raise ValueError("Webhook payload exceeds maximum size")

        # Store signing headers if provided (they're ephemeral per-request,
        # but we need them for retries since timestamps change)
        metadata = {}
        if signing_headers:
            metadata["original_signing_headers"] = signing_headers

        retry_id = self._generate_id()
        next_attempt_at = now  # First attempt is immediate

        self._db.execute(
            """INSERT INTO webhook_retry_queue
               (id, webhook_id, event_type, payload, endpoint_url,
                attempt_count, max_attempts, next_attempt_at,
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
            "next_attempt_at": next_attempt_at,
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

        current_attempt = row["attempt_count"]
        max_attempts = row["max_attempts"]
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
                   attempt_count = ?, next_attempt_at = ?, updated_at = ?
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
            "next_attempt_at": next_attempt_at,
            "delay_ms": delay,
        }

    def get_pending_retries(self, limit: int = 100) -> List[Dict[str, Any]]:
        """
        Get all webhook deliveries that are due for retry.

        Called by the scheduler to process pending retries.
        """
        now = self._get_timestamp()
        rows = self._db.fetch_all(
            """SELECT * FROM webhook_retry_queue
               WHERE status IN ('pending', 'retrying')
                 AND next_attempt_at <= ?
               ORDER BY next_attempt_at ASC
               LIMIT ?""",
            (now, limit),
        )
        results = []
        for row in rows:
            data = dict(row)
            if data.get("payload") and isinstance(data["payload"], str):
                try:
                    data["payload"] = json.loads(data["payload"])
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
        """Manually retry a dead letter entry."""
        now = self._get_timestamp()
        self._db.execute(
            """UPDATE webhook_retry_queue
               SET status = 'retrying', attempt_count = 0,
                   next_attempt_at = ?, updated_at = ?
               WHERE id = ? AND status = 'dead_letter'""",
            (now, now, retry_id),
        )
        logger.info(f"Manually retried dead letter entry {retry_id}")
        return True

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
        self._db.execute(
            """DELETE FROM webhook_retry_queue
               WHERE status IN ('delivered', 'dead_letter')
                 AND updated_at < ?""",
            (cutoff,),
        )

        # Auto-abandon stuck retrying records
        self._db.execute(
            """UPDATE webhook_retry_queue
               SET status = 'dead_letter', updated_at = ?
               WHERE status = 'retrying'
                 AND created_at < ?""",
            (now, cutoff),
        )

        logger.debug("Cleaned up expired webhook retry records")
        return 1

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
