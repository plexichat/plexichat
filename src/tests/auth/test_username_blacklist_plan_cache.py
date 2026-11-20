"""Regression tests for the username-blacklist derived-plan cache.

``BlacklistManager.is_blocked`` sits on the registration and profile-update
paths. It used to issue ``SELECT * FROM username_blacklist`` on *every* check
and re-normalise every literal pattern (Unicode NFKD + leetspeak + regex) each
time. The plan cache keeps one short-TTL, bounded, process-local derived plan
instead.

Before/after on this box (40 literals + 2 regexes, 50 distinct usernames,
``pytest -n 0 src/tests/auth/test_username_blacklist_plan_cache.py``):

    uncached (TTL forced to 0): 50 table reads, 2050 normalisations
    cached   (default TTL):      1 table read,   90 normalisations

The counters below pin that deterministic difference; the wall-clock figure is
intentionally not asserted because the shared test host is noisy.
"""

import time
from typing import Any, Dict, List

import pytest

from src.core.auth import blacklist as bl_module
from src.core.auth.blacklist import BlacklistManager


class _CountingDB:
    """Minimal DB double that only answers the blacklist SELECT."""

    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows
        self.selects = 0

    def fetch_all(self, query: str, params=()) -> List[Dict[str, Any]]:
        assert "username_blacklist" in query
        self.selects += 1
        return [dict(row) for row in self.rows]

    def execute(self, query: str, params=()) -> None:
        if "INSERT INTO username_blacklist" in query:
            self.rows.append(
                {
                    "id": len(self.rows) + 1,
                    "pattern": params[0],
                    "is_regex": int(bool(params[1])),
                    "reason": params[2],
                    "created_by": params[3],
                }
            )
        elif "DELETE FROM username_blacklist" in query:
            self.rows[:] = [row for row in self.rows if row["id"] != params[0]]
        else:
            raise AssertionError(f"unexpected write: {query}")


def _rows(count: int = 40) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = [
        {
            "id": i + 1,
            "pattern": f"reserved{i}",
            "is_regex": 0,
            "reason": f"reserved-{i}",
            "created_by": 1,
        }
        for i in range(count)
    ]
    rows.append(
        {
            "id": 901,
            "pattern": r"^spam[0-9]+$",
            "is_regex": 1,
            "reason": "spam",
            "created_by": 1,
        }
    )
    rows.append(
        {
            "id": 902,
            "pattern": r"^(?:free|cheap).*crypto$",
            "is_regex": 1,
            "reason": "promo",
            "created_by": 1,
        }
    )
    return rows


def _normalize_count(monkeypatch) -> List[int]:
    calls: List[int] = []
    original = BlacklistManager._normalize

    def counting(self, text):
        calls.append(1)
        return original(self, text)

    monkeypatch.setattr(BlacklistManager, "_normalize", counting)
    return calls


def test_plan_cache_batches_reads_and_preparation(monkeypatch):
    db = _CountingDB(_rows())
    manager = BlacklistManager(db)
    normalizes = _normalize_count(monkeypatch)

    for i in range(50):
        blocked, _ = manager.is_blocked(f"ordinary_user_{i}")
        assert blocked is False

    # Before: one full-table SELECT and O(patterns) normalisations per call.
    # After: one SELECT for the whole burst; patterns are normalised once at
    # plan build and the per-call work is just the username itself.
    assert db.selects == 1
    assert len(normalizes) == 50 + 40  # username once per call + literals once

    # Same cached plan still produces the exact verdicts, including regex rows.
    assert manager.is_blocked("reserved7")[1] == "reserved-7"
    assert manager.is_blocked("spam123")[1] == "spam"
    assert manager.is_blocked("FREE stuff CRYPTO")[1] == "promo"
    assert db.selects == 1


def test_uncached_path_is_equivalent_but_does_the_work_per_call(monkeypatch):
    """Pin the 'before' behaviour by forcing the plan to expire each call."""
    monkeypatch.setattr(bl_module, "_PLAN_TTL_SECONDS", 0.0)
    db = _CountingDB(_rows())
    manager = BlacklistManager(db)
    normalizes = _normalize_count(monkeypatch)

    for i in range(5):
        assert manager.is_blocked(f"ordinary_user_{i}")[0] is False

    assert db.selects == 5
    assert len(normalizes) == 5 * (1 + 40)
    assert manager.is_blocked("spam9")[1] == "spam"


def test_local_write_invalidates_without_waiting_for_ttl(monkeypatch):
    db = _CountingDB([])
    manager = BlacklistManager(db)

    assert manager.is_blocked("admin")[0] is False
    assert db.selects == 1

    new_id = manager.add_pattern("admin", reason="reserved")
    assert new_id == 0  # fake DB has no last_insert_id helper

    # The fresh row must be visible on the very next check, not after the
    # cross-node TTL window.
    assert manager.is_blocked("admin") == (True, "reserved")
    assert db.selects == 2

    db.rows.append(
        {
            "id": 42,
            "pattern": "another",
            "is_regex": 0,
            "reason": "also reserved",
            "created_by": 1,
        }
    )
    manager.remove_pattern(42)
    # remove_pattern is a local write too; the next check rereads the table.
    assert manager.is_blocked("another")[0] is False
    assert db.selects == 3


def test_cross_node_change_is_picked_up_after_ttl(monkeypatch):
    """A write by another node cannot leave a stale verdict past the TTL."""
    monkeypatch.setattr(bl_module, "_PLAN_TTL_SECONDS", 0.01)
    db = _CountingDB([])
    manager = BlacklistManager(db)

    assert manager.is_blocked("shadow")[0] is False
    db.rows.append(
        {
            "id": 7,
            "pattern": "shadow",
            "is_regex": 0,
            "reason": "banned elsewhere",
            "created_by": 1,
        }
    )
    # No local invalidation happened: only the bounded TTL makes this visible.
    time.sleep(0.02)
    assert manager.is_blocked("shadow") == (True, "banned elsewhere")
    assert db.selects == 2


def test_db_failure_fails_closed(monkeypatch):
    """A broken read propagates; it never fabricates a 'not blocked' verdict."""

    class BrokenDB(_CountingDB):
        def fetch_all(self, query, params=()):
            raise RuntimeError("database unavailable")

    manager = BlacklistManager(BrokenDB([]))
    with pytest.raises(RuntimeError):
        manager.is_blocked("anything")


def test_oversized_catalogue_is_not_cached(monkeypatch):
    """Correctness never depends on the bounded cache."""
    monkeypatch.setattr(bl_module, "_MAX_CACHED_PATTERNS", 2)
    db = _CountingDB(_rows(5))
    manager = BlacklistManager(db)

    for _ in range(3):
        assert manager.is_blocked("ordinary")[0] is False
    assert db.selects == 3
    assert manager.is_blocked("reserved1")[1] == "reserved-1"
