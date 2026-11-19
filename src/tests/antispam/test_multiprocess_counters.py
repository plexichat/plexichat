"""Multi-worker DM spam counter regression (``--scale backend=N``).

Valkey is forced down and each "worker" gets fresh process-local counter
state via ``reset_local_windows``. The two workers share one database, so
the rate/duplicate budgets must still be enforced GLOBALLY: a per-process
dict would let N workers each spend the full budget (Nx the spam).

Counter state uses the shared SQL tier here, so no live Valkey is needed.
"""

from __future__ import annotations

import pytest

import src.core.antispam.detector as detector_mod
from src.core.antispam.detector import DMSpamDetector

pytestmark = pytest.mark.integration

T0_MS = 1_000_000_000_000


@pytest.fixture(autouse=True)
def _valkey_down_shared_db(db, monkeypatch):
    """Two workers, no Valkey, one shared database."""
    monkeypatch.setattr(detector_mod, "get_client", lambda: None)
    detector_mod.reset_local_windows()
    detector_mod.AtomicWindowCounter._ensure_shared_table(db)
    db.execute("DELETE FROM dm_spam_counters")
    yield
    detector_mod.reset_local_windows()


def _worker(db, now=T0_MS):
    """Build a worker with a frozen clock and fresh local counter state."""
    detector = DMSpamDetector(db)
    state = {"now": now}

    def _ts():
        return state["now"]

    detector._get_timestamp = _ts
    detector._tick = lambda ms: state.update(now=state["now"] + ms)
    return detector, state


def test_rate_limit_is_global_across_workers(db):
    worker_a, _ = _worker(db)
    for i in range(DMSpamDetector.DEFAULT_RATE_LIMIT):
        assert worker_a.check_message(1, 2, f"hello message {i}")[0] is True

    # New process: local counters start empty, but the shared DB does not.
    detector_mod.reset_local_windows()
    worker_b, _ = _worker(db)
    allowed, reason = worker_b.check_message(1, 2, "hello from worker b")
    assert allowed is False
    assert reason is not None and "Rate limit exceeded" in reason
    rows = db.fetch_all(
        "SELECT count FROM dm_spam_counters WHERE cache_key = ?",
        ("dm_rate:1:2",),
    )
    assert len(rows) == 1
    assert rows[0]["count"] == DMSpamDetector.DEFAULT_RATE_LIMIT + 1


def test_duplicate_budget_is_global_across_workers(db):
    content = "buy crypto now"
    worker_a, _ = _worker(db)
    for _ in range(DMSpamDetector.DEFAULT_DUPLICATE_THRESHOLD):
        assert worker_a.check_message(1, 3, content)[0] is True

    detector_mod.reset_local_windows()
    worker_b, _ = _worker(db)
    allowed, reason = worker_b.check_message(1, 3, content)
    assert allowed is False
    assert reason == "Duplicate message detected"


def test_shared_counter_registry_is_bounded(db, monkeypatch):
    monkeypatch.setattr(detector_mod, "_SHARED_COUNTER_SWEEP_INTERVAL_S", 0.0)
    monkeypatch.setattr(detector_mod, "_SHARED_COUNTER_MAX_ROWS", 3)
    counter = detector_mod.AtomicWindowCounter()
    counter.configure(db)
    now = float(T0_MS // 1000)

    # A flood of distinct (user, conversation, hash) keys must not grow the
    # shared registry: the sweep keeps the newest MAX_ROWS rows only.
    for i in range(8):
        assert counter.record_and_count(f"key-{i}", now, 60, 5) is True
    rows = db.fetch_all("SELECT id FROM dm_spam_counters")
    assert len(rows) <= 3


def test_shared_counter_window_expires(db):
    counter = detector_mod.AtomicWindowCounter()
    counter.configure(db)
    now = float(T0_MS // 1000)

    assert counter.record_and_count("spent", now, 60, 1) is True
    assert counter.record_and_count("spent", now, 60, 1) is False

    # Past the largest allowed window the old bucket is swept (TTL) and a
    # fresh window starts with a clean budget.
    future = now + detector_mod.MAX_FILTER_WINDOW_SECONDS + 2
    assert counter.record_and_count("spent", future, 60, 1) is True
