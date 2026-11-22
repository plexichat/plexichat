"""Regression: the per-IP pending-state cap is atomic under concurrency.

Pre-fix, ``OAuthStateManager.create_state`` did a ``SELECT COUNT(*) ...``
(``_check_ip_rate_limit``) and a plain ``INSERT`` as two separate statements.
N workers could all observe ``count == max - 1`` and all insert, minting N
states when only one slot remained. The fix folds the count guard into the
INSERT (``_ATOMIC_STATE_INSERT_SQL``) so the compare and the write are one
operation.

The fake DB below uses two barriers to make the race deterministic: every
thread completes the advisory count read before any thread is allowed to
perform its INSERT. The legacy flow therefore over-admits, while the hardened
manager admits exactly the configured number.
"""

from __future__ import annotations

import sqlite3
import threading

from src.core.auth.oauth.state import OAuthStateManager

IP = "203.0.113.7"
CAP = 1
N = 4


class BarrierDB:
    """Minimal Database stand-in backed by sqlite with forced interleaving."""

    type = "sqlite"

    def __init__(
        self, read_barrier: threading.Barrier, insert_barrier: threading.Barrier
    ):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._read_barrier = read_barrier
        self._insert_barrier = insert_barrier
        self._lock = threading.RLock()
        self._conn.execute(
            """CREATE TABLE auth_oauth_states (
                id INTEGER PRIMARY KEY,
                state_hash TEXT NOT NULL UNIQUE,
                provider TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                used INTEGER DEFAULT 0,
                nonce_hash TEXT,
                pkce_challenge TEXT,
                ip_address TEXT,
                cookie_issued INTEGER DEFAULT 0
            )"""
        )
        self._conn.commit()

    def convert_schema(self, schema: str) -> str:
        return schema

    def begin_transaction(self) -> None:
        pass

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def execute(self, sql, params=()):
        if "INSERT INTO auth_oauth_states" in sql and "SELECT" not in sql:
            # All callers have finished the advisory count read; only now may
            # the first INSERT proceed.
            self._insert_barrier.wait(timeout=10)
        with self._lock:
            return self._conn.execute(sql, params)

    def fetch_one(self, sql, params=()):
        is_cap_read = "COUNT(*) as count FROM auth_oauth_states" in sql
        with self._lock:
            cur = self._conn.execute(sql, params)
            row = cur.fetchone()
            result = dict(row) if row is not None else None
        if is_cap_read:
            # Every worker reads the same (stale) snapshot before any INSERT.
            self._read_barrier.wait(timeout=10)
        return result

    def fetch_all(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def count(self) -> int:
        with self._lock:
            return int(
                self._conn.execute(
                    "SELECT COUNT(*) AS n FROM auth_oauth_states WHERE ip_address = ?",
                    (IP,),
                ).fetchone()["n"]
            )


def test_legacy_check_then_insert_overadmits():
    """Non-vacuity: the old two-statement flow defeats the cap under load."""
    read_barrier = threading.Barrier(N)
    insert_barrier = threading.Barrier(N)
    db = BarrierDB(read_barrier, insert_barrier)

    def legacy_create():
        # The exact pre-fix sequence: read count, compare, then plain INSERT.
        row = db.fetch_one(
            "SELECT COUNT(*) as count FROM auth_oauth_states "
            "WHERE ip_address = ? AND used = 0 AND expires_at > ?",
            (IP, 0),
        )
        if row and row["count"] >= CAP:
            return False
        db.execute(
            "INSERT INTO auth_oauth_states "
            "(id, state_hash, provider, redirect_uri, created_at, expires_at, "
            "ip_address) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                threading.get_ident(),
                f"h{threading.get_ident()}",
                "google",
                "uri",
                0,
                10_000,
                IP,
            ),
        )
        return True

    threads = [threading.Thread(target=legacy_create) for _ in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads)
    # All N raced past the check when only CAP slot existed -> over-admission.
    assert db.count() == N > CAP


def test_guarded_insert_caps_pending_states():
    """Hardened create_state admits exactly CAP states, not N."""
    read_barrier = threading.Barrier(N)
    insert_barrier = threading.Barrier(N)
    db = BarrierDB(read_barrier, insert_barrier)
    manager = OAuthStateManager(
        db, {"max_states_per_ip": CAP, "state_ttl_seconds": 600}
    )

    results: list[object] = [None] * N

    def create(idx):
        results[idx] = manager.create_state(
            provider="google",
            redirect_uri="https://example.com/cb",
            ip_address=IP,
        )

    threads = [threading.Thread(target=create, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads)

    assert db.count() == CAP
    succeeded = sum(r is not None for r in results)
    assert succeeded == CAP


def test_regression_would_catch_guard_removal(monkeypatch):
    """Mutate the production SQL to drop the guard: the test must then fail.

    The replacement accepts the same 13 bindings but ignores the three guard
    parameters, emulating the old unguarded INSERT while still driving the real
    ``create_state`` code path. If this over-admits, the regression above is
    genuinely pinned to the guard rather than passing vacuously.
    """
    import src.core.auth.oauth.state as state_module

    unguarded = """
    INSERT INTO auth_oauth_states
        (id, state_hash, provider, redirect_uri, created_at, expires_at,
         nonce_hash, pkce_challenge, ip_address, cookie_issued)
    SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    WHERE (SELECT ? IS NOT NULL)
       OR (SELECT ? IS NOT NULL)
       OR (SELECT ? IS NOT NULL)
    """
    monkeypatch.setattr(state_module, "_ATOMIC_STATE_INSERT_SQL", unguarded)

    read_barrier = threading.Barrier(N)
    insert_barrier = threading.Barrier(N)
    db = BarrierDB(read_barrier, insert_barrier)
    manager = OAuthStateManager(
        db, {"max_states_per_ip": CAP, "state_ttl_seconds": 600}
    )

    def create(idx):
        manager.create_state(
            provider="google",
            redirect_uri="https://example.com/cb",
            ip_address=IP,
        )

    threads = [threading.Thread(target=create, args=(i,)) for i in range(N)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
    assert not any(t.is_alive() for t in threads)
    assert db.count() == N > CAP
