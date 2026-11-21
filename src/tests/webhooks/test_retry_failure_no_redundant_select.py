"""Regression: a failing delivery batch must not re-read counters it already owns.

``get_pending_retries`` claims rows with ``SELECT *``, so each claimed record
already carries ``attempt_count`` and ``max_attempts``. The delivery loop used
to call ``mark_failure`` for every failed row, and that method issued another
``SELECT attempt_count, max_attempts ...`` before its UPDATE. A batch of N
failed deliveries therefore paid for N redundant database reads on top of N
writes. The delivery loop now uses a fast path that reuses the counters on the
claimed record; the public ``mark_failure`` entry point keeps its exact
behaviour for callers that only hold a retry id.
"""

from src.core.webhooks.delivery import process_due_retries
from src.core.webhooks.retry import WebhookRetryQueue


class _CountingDB:
    """Proxy that counts the redundant counter read without changing results."""

    def __init__(self, db):
        self._db = db
        self.counter_selects = 0
        self.executes = 0

    def __getattr__(self, name):
        return getattr(self._db, name)

    def fetch_one(self, sql, params=None):
        if "SELECT attempt_count, max_attempts" in sql:
            self.counter_selects += 1
        return self._db.fetch_one(sql, params)

    def fetch_all(self, sql, params=None):
        return self._db.fetch_all(sql, params)

    def execute(self, sql, params=None):
        self.executes += 1
        return self._db.execute(sql, params)


class _AlwaysFails:
    """Deliverer whose every attempt fails at the transport layer."""

    def __init__(self):
        self.calls = 0

    def deliver(self, record):
        self.calls += 1
        return 503


def _enqueue(queue, count, endpoint="https://example.com/hook"):
    for i in range(count):
        queue.enqueue(
            webhook_id=i + 1,
            event_type="message.created",
            payload={"event": "message.created", "n": i},
            endpoint_url=endpoint,
        )


def test_failing_batch_does_not_reload_counters(db):
    counting = _CountingDB(db)
    queue = WebhookRetryQueue(counting)
    _enqueue(queue, 12)

    stats = process_due_retries(queue, _AlwaysFails(), limit=100)
    assert stats["processed"] == 12
    assert stats["failed"] == 12
    # Before the fix this was 12: one redundant SELECT per failed row.
    assert counting.counter_selects == 0
    # The writes (atomic claims + outcomes) still happen through the DB.
    assert counting.executes >= 12


def test_fast_path_keeps_backoff_and_dead_letter_behaviour(db):
    counting = _CountingDB(db)
    queue = WebhookRetryQueue(counting)
    _enqueue(queue, 3)

    # Two cycles: both are transport failures, so attempts advance toward the
    # dead-letter transition. Counters must come from the claimed record so the
    # backoff/attempt math is identical to the public mark_failure path.
    first = process_due_retries(queue, _AlwaysFails(), limit=100)
    assert first["failed"] == 3
    for attempt in range(WebhookRetryQueue.MAX_RETRIES - 1):
        # Backoff pushed the next attempt into the future; make rows due again
        # so the following cycle actually drives the counter forward.
        db.execute(
            "UPDATE webhook_retry_queue SET next_retry_at = 0 WHERE status = 'retrying'"
        )
        stats = process_due_retries(queue, _AlwaysFails(), limit=100)
        assert stats["failed"] == 3

    rows = db.fetch_all(
        "SELECT status, attempt_count FROM webhook_retry_queue ORDER BY id"
    )
    assert len(rows) == 3
    assert all(row["status"] == "dead_letter" for row in rows)
    assert all(row["attempt_count"] == WebhookRetryQueue.MAX_RETRIES for row in rows)
    assert counting.counter_selects == 0


def test_public_mark_failure_still_reads_and_validates_the_row(db):
    """Callers holding only a retry id keep the original SELECT + validation."""
    counting = _CountingDB(db)
    queue = WebhookRetryQueue(counting)
    queued = queue.enqueue(
        webhook_id=1,
        event_type="message.created",
        payload={"n": 1},
        endpoint_url="https://example.com/hook",
    )

    result = queue.mark_failure(queued["id"], 503, "upstream down")
    assert result["status"] == "retrying"
    assert result["attempts"] == 1
    assert counting.counter_selects == 1

    import pytest

    with pytest.raises(ValueError, match="not found"):
        queue.mark_failure(999999)
