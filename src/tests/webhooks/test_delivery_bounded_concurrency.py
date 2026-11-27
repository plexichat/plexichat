"""Regression: webhook fan-out delivery is bounded-concurrent, not serial.

A batch of due retry rows used to be POSTed strictly one at a time, so a batch
of N slow endpoints cost N round trips of wall clock. The scheduler now drains
a batch with a bounded worker pool while keeping every security gate and all
queue updates on the calling thread in claim order.
"""

import threading
import time

import pytest

from src.core.webhooks.delivery import (
    DEFAULT_DELIVERY_CONCURRENCY,
    MAX_DELIVERY_CONCURRENCY,
    WebhookDeliveryJob,
    WebhookDeliveryError,
    process_due_retries,
)
from src.core.webhooks.retry import WebhookRetryQueue


@pytest.fixture
def retry_queue(db):
    return WebhookRetryQueue(db)


def _enqueue(queue, count):
    return [
        queue.enqueue(
            webhook_id=i + 1,
            event_type="message.created",
            payload={"event": "message.created", "n": i},
            endpoint_url=f"https://example.com/hook/{i}",
        )
        for i in range(count)
    ]


class _BlockingDeliverer:
    """Records peak concurrency; scripts a status/exception per record id."""

    def __init__(self, delay=0.02, responses=None):
        self.delay = delay
        self.responses = responses or {}
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.seen = []

    def deliver(self, record):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.seen.append(record["id"])
        try:
            time.sleep(self.delay)
            outcome = self.responses.get(record["id"], 200)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            with self.lock:
                self.active -= 1


class _RecordingQueue:
    """Wraps a queue to capture the order queue updates are applied in."""

    def __init__(self, queue):
        self._queue = queue
        self.calls = []

    def get_pending_retries(self, limit=100):
        return self._queue.get_pending_retries(limit)

    def mark_success(self, retry_id, status):
        self.calls.append(("ok", retry_id))
        return self._queue.mark_success(retry_id, status)

    def mark_failure(self, retry_id, status, error):
        self.calls.append(("fail", retry_id))
        return self._queue.mark_failure(retry_id, status, error)


class TestBoundedConcurrency:
    def test_default_is_sequential(self, retry_queue):
        _enqueue(retry_queue, 6)
        deliverer = _BlockingDeliverer()
        stats = process_due_retries(retry_queue, deliverer)
        assert stats["delivered"] == 6
        assert deliverer.max_active == 1

    def test_bounded_parallel_delivery(self, retry_queue):
        records = _enqueue(retry_queue, 12)
        deliverer = _BlockingDeliverer()
        stats = process_due_retries(retry_queue, deliverer, max_concurrency=4)
        assert stats == {
            "processed": 12,
            "delivered": 12,
            "failed": 0,
            "transport_errors": 0,
        }
        # Reaches the configured ceiling but never exceeds it.
        assert deliverer.max_active == 4
        assert set(deliverer.seen) == {r["id"] for r in records}

    def test_concurrency_is_capped(self, retry_queue):
        _enqueue(retry_queue, 40)
        deliverer = _BlockingDeliverer()
        process_due_retries(retry_queue, deliverer, max_concurrency=10_000)
        assert deliverer.max_active <= MAX_DELIVERY_CONCURRENCY

    def test_parallel_batch_is_faster_than_serial(self, retry_queue):
        _enqueue(retry_queue, 8)
        # The per-delivery delay has to dominate the fixed cost of the queue
        # updates, or the ratio measures the database rather than the pool: at
        # 0.03s the batch is 0.24s of sleeps behind ~0.06s of updates, and
        # test:timing_under_load -- which runs two busy-loop burners per CPU on
        # purpose -- measured 2.7x against the 3x asserted here. At 0.25s the
        # sleeps are decisive (2s serial against ~0.3s parallel) and the same
        # 3x assertion holds.
        serial = _BlockingDeliverer(delay=0.25)
        t0 = time.perf_counter()
        process_due_retries(retry_queue, serial)
        serial_s = time.perf_counter() - t0

        retry_queue2 = WebhookRetryQueue(retry_queue._db)
        _enqueue(retry_queue2, 8)
        parallel = _BlockingDeliverer(delay=0.25)
        t0 = time.perf_counter()
        process_due_retries(retry_queue2, parallel, max_concurrency=8)
        parallel_s = time.perf_counter() - t0

        assert parallel_s < serial_s / 3

    def test_empty_batch_is_a_noop(self, retry_queue):
        stats = process_due_retries(
            retry_queue, _BlockingDeliverer(), max_concurrency=8
        )
        assert stats["processed"] == 0


class TestSameOutcomes:
    def test_mixed_statuses_under_concurrency(self, retry_queue):
        records = _enqueue(retry_queue, 4)
        deliverer = _BlockingDeliverer(
            responses={
                records[0]["id"]: 200,
                records[1]["id"]: 503,
                records[2]["id"]: WebhookDeliveryError("connection refused"),
                records[3]["id"]: RuntimeError("boom"),
            }
        )
        stats = process_due_retries(retry_queue, deliverer, max_concurrency=4)
        assert stats == {
            "processed": 4,
            "delivered": 1,
            "failed": 3,
            "transport_errors": 2,
        }
        for r in records:
            row = retry_queue._db.fetch_one(
                "SELECT status, last_error FROM webhook_retry_queue WHERE id = ?",
                (r["id"],),
            )
            assert row["status"] in ("delivered", "retrying")

    def test_queue_updates_stay_in_claim_order(self, retry_queue):
        records = _enqueue(retry_queue, 8)
        # Earlier records are slower, so HTTP results complete out of order.
        times = {r["id"]: (0.03 if i < 3 else 0.0) for i, r in enumerate(records)}

        class _VariableDeliverer:
            def deliver(self, record):
                time.sleep(times[record["id"]])
                return 200

        recording = _RecordingQueue(retry_queue)
        stats = process_due_retries(recording, _VariableDeliverer(), max_concurrency=8)
        assert stats["delivered"] == 8
        due_ids = [r["id"] for r in records]
        assert [rid for _, rid in recording.calls] == due_ids


class TestJobConfig:
    @pytest.mark.parametrize(
        "cfg,expected",
        [
            ({}, DEFAULT_DELIVERY_CONCURRENCY),
            ({"delivery_concurrency": 3}, 3),
            ({"delivery_concurrency": 0}, 1),
            ({"delivery_concurrency": 9999}, MAX_DELIVERY_CONCURRENCY),
            ({"delivery_concurrency": "bad"}, DEFAULT_DELIVERY_CONCURRENCY),
        ],
    )
    def test_concurrency_config_is_bounded(self, cfg, expected):
        assert WebhookDeliveryJob(None, cfg)._concurrency() == expected
