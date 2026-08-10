"""
Tests for WebhookRetryQueue — exponential backoff delivery retries,
dead letter queue, cleanup and statistics.

The queue is backed by the real ``webhook_retry_queue`` table created by
the chat_tracking schema (migration 000) so every query exercises the
production column set.
"""

import json
import random
import time
from unittest.mock import patch

import pytest

from src.core.webhooks.retry import WebhookRetryQueue


@pytest.fixture
def retry_queue(db):
    """A fresh queue instance over the function-scoped test database."""
    return WebhookRetryQueue(db)


def _due_payload():
    return {"content": "hello", "embed": {"title": "Hi"}}


class TestCalculateDelay:
    """Exponential backoff math."""

    def test_delay_grows_exponentially_with_attempt(self, retry_queue):
        with patch.object(random, "randint", return_value=0):
            delay_1 = retry_queue._calculate_delay(1)
            delay_2 = retry_queue._calculate_delay(2)
            delay_3 = retry_queue._calculate_delay(3)
        assert delay_1 == WebhookRetryQueue.BASE_DELAY_MS * 2  # 2s
        assert delay_2 == WebhookRetryQueue.BASE_DELAY_MS * 4  # 4s
        assert delay_3 == WebhookRetryQueue.BASE_DELAY_MS * 8  # 8s

    def test_delay_capped_at_max(self, retry_queue):
        with patch.object(random, "randint", return_value=0):
            delay = retry_queue._calculate_delay(20)
        assert delay == WebhookRetryQueue.MAX_DELAY_MS

    def test_delay_includes_jitter(self, retry_queue):
        with patch.object(random, "randint", return_value=WebhookRetryQueue.JITTER_MS):
            delay = retry_queue._calculate_delay(1)
        assert (
            delay == WebhookRetryQueue.BASE_DELAY_MS * 2 + WebhookRetryQueue.JITTER_MS
        )


class TestEnqueue:
    def test_enqueue_creates_pending_record(self, db, retry_queue):
        record = retry_queue.enqueue(
            webhook_id=101,
            event_type="message.created",
            payload=_due_payload(),
            endpoint_url="https://example.com/hook",
            signing_headers={"X-Signature": "abc", "X-Timestamp": "123"},
        )

        assert record["id"] > 0
        assert record["webhook_id"] == 101
        assert record["event_type"] == "message.created"
        assert record["endpoint_url"] == "https://example.com/hook"
        assert record["attempt_count"] == 0
        assert record["max_attempts"] == WebhookRetryQueue.MAX_RETRIES
        assert record["status"] == "pending"
        assert record["next_retry_at"] <= int(time.time() * 1000)

        row = db.fetch_one(
            "SELECT * FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row is not None
        assert row["status"] == "pending"
        assert row["attempt_count"] == 0
        assert row["max_attempts"] == WebhookRetryQueue.MAX_RETRIES
        assert row["event_type"] == "message.created"
        assert row["endpoint_url"] == "https://example.com/hook"
        assert json.loads(row["payload"]) == _due_payload()
        metadata = json.loads(row["metadata"])
        assert metadata == {
            "original_signing_headers": {
                "X-Signature": "abc",
                "X-Timestamp": "123",
            }
        }

    def test_enqueue_metadata_null_without_signing_headers(self, db, retry_queue):
        record = retry_queue.enqueue(
            webhook_id=202,
            event_type="server.update",
            payload={"field": "value"},
            endpoint_url="https://example.com/hook",
        )
        row = db.fetch_one(
            "SELECT metadata FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row is not None
        assert row["metadata"] is None

    def test_enqueue_oversized_payload_rejected(self, retry_queue):
        big = "x" * (WebhookRetryQueue.MAX_PAYLOAD_SIZE + 1)
        with pytest.raises(ValueError, match="exceeds maximum size"):
            retry_queue.enqueue(
                webhook_id=303,
                event_type="message.created",
                payload={"data": big},
                endpoint_url="https://example.com/hook",
            )


class TestMarkSuccess:
    def test_mark_success_marks_delivered(self, db, retry_queue):
        record = retry_queue.enqueue(
            webhook_id=101, event_type="e", payload={}, endpoint_url="http://x"
        )
        retry_queue.mark_success(record["id"], 200)

        row = db.fetch_one(
            "SELECT * FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row["status"] == "delivered"
        assert row["response_status"] == 200
        assert row["attempt_count"] == 1
        assert row["delivered_at"] is not None


class TestMarkFailure:
    def test_mark_failure_schedules_retry(self, db, retry_queue, monkeypatch):
        record = retry_queue.enqueue(
            webhook_id=101, event_type="e", payload={}, endpoint_url="http://x"
        )
        monkeypatch.setattr(retry_queue, "_get_timestamp", lambda: 1_000_000_000_000)
        monkeypatch.setattr(random, "randint", lambda a, b: 0)

        result = retry_queue.mark_failure(record["id"], 500, "upstream down")

        assert result["status"] == "retrying"
        assert result["attempts"] == 1
        # Base 1000ms * 2^1 = 2000ms + zero jitter
        assert result["delay_ms"] == 2000
        assert result["next_retry_at"] == 1_000_000_000_000 + 2000

        row = db.fetch_one(
            "SELECT * FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row["status"] == "retrying"
        assert row["attempt_count"] == 1
        assert row["last_error"] == "upstream down"
        assert row["response_status"] == 500
        assert row["next_retry_at"] == 1_000_000_000_000 + 2000

    def test_mark_failure_unknown_id_raises(self, retry_queue):
        with pytest.raises(ValueError, match="not found"):
            retry_queue.mark_failure(999999)

    def test_mark_failure_moves_to_dead_letter_after_max(self, db, retry_queue):
        record = retry_queue.enqueue(
            webhook_id=101, event_type="e", payload={}, endpoint_url="http://x"
        )
        # MAX_RETRIES = 5: attempts 1-4 schedule retries, the 5th dead-letters.
        for _ in range(WebhookRetryQueue.MAX_RETRIES - 1):
            result = retry_queue.mark_failure(record["id"], 500)
            assert result["status"] == "retrying"

        result = retry_queue.mark_failure(record["id"], 500, "permanent")
        assert result["status"] == "dead_letter"
        assert result["attempts"] == WebhookRetryQueue.MAX_RETRIES

        row = db.fetch_one(
            "SELECT * FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row["status"] == "dead_letter"
        assert row["attempt_count"] == WebhookRetryQueue.MAX_RETRIES
        assert row["last_error"] == "permanent"


class TestGetPendingRetries:
    def _enqueue(self, retry_queue, webhook_id, payload):
        return retry_queue.enqueue(
            webhook_id=webhook_id,
            event_type="message.created",
            payload=payload,
            endpoint_url="https://example.com/hook",
        )

    def test_returns_only_due_records_ordered_by_next_retry_at(self, db, retry_queue):
        a = self._enqueue(retry_queue, 1, {"m": "a"})
        b = self._enqueue(retry_queue, 2, {"m": "b"})
        c = self._enqueue(retry_queue, 3, {"m": "c"})
        # Force deterministic scheduling: b oldest, a next, c in the future.
        db.execute(
            "UPDATE webhook_retry_queue SET next_retry_at = ? WHERE id = ?",
            (1_000_000, b["id"]),
        )
        db.execute(
            "UPDATE webhook_retry_queue SET next_retry_at = ? WHERE id = ?",
            (2_000_000, a["id"]),
        )
        db.execute(
            "UPDATE webhook_retry_queue SET next_retry_at = ? WHERE id = ?",
            (9_000_000_000_000, c["id"]),
        )

        due = retry_queue.get_pending_retries()
        ids = [r["id"] for r in due]
        assert ids == [b["id"], a["id"]]

    def test_limit_respected(self, db, retry_queue):
        first = self._enqueue(retry_queue, 1, {"m": "a"})
        second = self._enqueue(retry_queue, 2, {"m": "b"})
        due = retry_queue.get_pending_retries(limit=1)
        assert len(due) == 1
        assert due[0]["id"] in (first["id"], second["id"])

    def test_retrying_not_due_is_excluded(self, db, retry_queue, monkeypatch):
        record = self._enqueue(retry_queue, 1, {"m": "a"})
        monkeypatch.setattr(retry_queue, "_get_timestamp", lambda: 1_000_000_000_000)
        monkeypatch.setattr(random, "randint", lambda a, b: 0)
        retry_queue.mark_failure(record["id"])  # next_retry_at = now + 2000
        # Freeze time at the failure moment: the retry is not due yet.
        monkeypatch.setattr(retry_queue, "_get_timestamp", lambda: 1_000_000_000_000)
        assert retry_queue.get_pending_retries() == []

    def test_payload_and_metadata_parsed_from_json(self, db, retry_queue):
        record = retry_queue.enqueue(
            webhook_id=7,
            event_type="message.created",
            payload=_due_payload(),
            endpoint_url="http://x",
            signing_headers={"X-Sig": "s"},
        )
        assert record["id"] > 0
        due = retry_queue.get_pending_retries()
        assert len(due) == 1
        assert due[0]["payload"] == _due_payload()
        assert due[0]["metadata"] == {"original_signing_headers": {"X-Sig": "s"}}

    def test_corrupt_payload_becomes_empty_dict(self, db, retry_queue):
        record = self._enqueue(retry_queue, 1, {"m": "a"})
        db.execute(
            "UPDATE webhook_retry_queue SET payload = ? WHERE id = ?",
            ("not-json{", record["id"]),
        )
        due = retry_queue.get_pending_retries()
        assert len(due) == 1
        assert due[0]["payload"] == {}


class TestDeadLetterQueue:
    def _dead_letter(self, retry_queue, webhook_id):
        record = retry_queue.enqueue(
            webhook_id=webhook_id,
            event_type="e",
            payload={},
            endpoint_url="http://x",
        )
        for _ in range(WebhookRetryQueue.MAX_RETRIES):
            retry_queue.mark_failure(record["id"], 500)
        return record

    def test_get_dead_letter_queue_filters_by_webhook(self, db, retry_queue):
        r1 = self._dead_letter(retry_queue, 101)
        self._dead_letter(retry_queue, 102)

        for_webhook = retry_queue.get_dead_letter_queue(webhook_id=101)
        assert [r["id"] for r in for_webhook] == [r1["id"]]

        all_dead = retry_queue.get_dead_letter_queue()
        assert len(all_dead) == 2

    def test_dead_letter_ordered_by_updated_at_desc(self, db, retry_queue, monkeypatch):
        ticks = {"now": 1_000_000_000_000}
        monkeypatch.setattr(retry_queue, "_get_timestamp", lambda: ticks["now"])
        first = self._dead_letter(retry_queue, 101)
        ticks["now"] += 10_000  # 10 seconds later
        second = self._dead_letter(retry_queue, 101)
        all_dead = retry_queue.get_dead_letter_queue(webhook_id=101)
        assert [r["id"] for r in all_dead] == [second["id"], first["id"]]

    def test_retry_dead_letter_resets_and_requeues(self, db, retry_queue):
        record = self._dead_letter(retry_queue, 101)

        assert retry_queue.retry_dead_letter(record["id"]) is True

        row = db.fetch_one(
            "SELECT * FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row["status"] == "retrying"
        assert row["attempt_count"] == 0
        assert row["next_retry_at"] <= int(time.time() * 1000)

    def test_retry_dead_letter_unknown_id_false(self, retry_queue):
        assert retry_queue.retry_dead_letter(999999) is False

    def test_retry_dead_letter_non_dead_letter_false(self, db, retry_queue):
        record = retry_queue.enqueue(
            webhook_id=1, event_type="e", payload={}, endpoint_url="http://x"
        )
        assert retry_queue.retry_dead_letter(record["id"]) is False


class TestCleanupExpired:
    def test_cleanup_gated_by_interval(self, db, retry_queue):
        record = retry_queue.enqueue(
            webhook_id=1, event_type="e", payload={}, endpoint_url="http://x"
        )
        retry_queue.mark_success(record["id"], 200)
        # Force the record to be older than the retention window.
        old = int(time.time() * 1000) - retry_queue.RETRY_WINDOW_MS - 1000
        db.execute(
            "UPDATE webhook_retry_queue SET updated_at = ? WHERE id = ?",
            (old, record["id"]),
        )

        assert retry_queue.cleanup_expired() == 1
        # A second call within CLEANUP_INTERVAL_MS must do nothing.
        assert retry_queue.cleanup_expired() == 0

    def test_cleanup_deletes_old_delivered_and_dead_letter(self, db, retry_queue):
        old = int(time.time() * 1000) - retry_queue.RETRY_WINDOW_MS - 1000
        for webhook_id in (1, 2):
            record = retry_queue.enqueue(
                webhook_id=webhook_id,
                event_type="e",
                payload={},
                endpoint_url="http://x",
            )
            retry_queue.mark_success(record["id"], 200)
            db.execute(
                "UPDATE webhook_retry_queue SET updated_at = ? WHERE id = ?",
                (old, record["id"]),
            )
        # A recent delivered record must survive the sweep.
        fresh = retry_queue.enqueue(
            webhook_id=3, event_type="e", payload={}, endpoint_url="http://x"
        )
        retry_queue.mark_success(fresh["id"], 200)

        assert retry_queue.cleanup_expired() == 2
        remaining = db.fetch_all("SELECT id FROM webhook_retry_queue")
        assert len(remaining) == 1
        assert remaining[0]["id"] == fresh["id"]

    def test_cleanup_abandons_stuck_retrying(self, db, retry_queue):
        record = retry_queue.enqueue(
            webhook_id=1, event_type="e", payload={}, endpoint_url="http://x"
        )
        retry_queue.mark_failure(record["id"], 500)
        old = int(time.time() * 1000) - retry_queue.RETRY_WINDOW_MS - 1000
        db.execute(
            "UPDATE webhook_retry_queue SET created_at = ? WHERE id = ?",
            (old, record["id"]),
        )

        assert retry_queue.cleanup_expired() == 1
        row = db.fetch_one(
            "SELECT status FROM webhook_retry_queue WHERE id = ?", (record["id"],)
        )
        assert row["status"] == "dead_letter"


class TestStats:
    def _dead_letter(self, retry_queue, webhook_id):
        record = retry_queue.enqueue(
            webhook_id=webhook_id,
            event_type="e",
            payload={},
            endpoint_url="http://x",
        )
        for _ in range(WebhookRetryQueue.MAX_RETRIES):
            retry_queue.mark_failure(record["id"], 500)
        return record

    def test_get_stats_overall_counts(self, db, retry_queue):
        pending = retry_queue.enqueue(
            webhook_id=1, event_type="e", payload={}, endpoint_url="http://x"
        )
        delivered = retry_queue.enqueue(
            webhook_id=2, event_type="e", payload={}, endpoint_url="http://x"
        )
        retry_queue.mark_success(delivered["id"], 200)
        self._dead_letter(retry_queue, 3)

        assert pending["status"] == "pending"
        stats = retry_queue.get_stats()
        assert stats["pending"] == 1
        assert stats["delivered"] == 1
        assert stats["dead_letter"] == 1
        assert stats["retrying"] == 0
        assert stats["total"] == 3

    def test_get_stats_filtered_by_webhook(self, db, retry_queue):
        retry_queue.enqueue(
            webhook_id=1, event_type="e", payload={}, endpoint_url="http://x"
        )
        retry_queue.enqueue(
            webhook_id=2, event_type="e", payload={}, endpoint_url="http://x"
        )
        stats = retry_queue.get_stats(webhook_id=1)
        assert stats["pending"] == 1
        assert stats["total"] == 1
