"""
Relational database storage backend for rate limiting.
Supports both SQLite and PostgreSQL via the Database abstraction.
"""

# pyright: reportAttributeAccessIssue=false
# Database class uses mixins which pyright's protocol checking doesn't fully recognize
import time
import json
from typing import Optional, List, Dict, Any
from src.core.database import Database
from .base import RateLimitStorage


def _is_lock_error(exc: Exception) -> bool:
    """Return True for transient SQLite lock errors worth retrying."""
    msg = str(exc).lower()
    return "database is locked" in msg or "database table is locked" in msg


class DatabaseStorage(RateLimitStorage):
    """Atomic relational database storage for rate limit buckets."""

    # F8: bound the bucket table so crafted distinct URLs cannot grow it
    # without limit. Rows always carry ``expires_at`` (TTL) and excess rows
    # beyond this cap are evicted oldest-first on the write path.
    MAX_BUCKET_ROWS = 50000
    DEFAULT_BUCKET_TTL = 86400.0
    # Class-level default so instances built via ``__new__`` (as some
    # tests do) still have the counter before ``__init__`` runs.
    _write_count = 0

    def __init__(self, db: Optional[Database] = None):
        """Initialize database storage."""
        self._db = db or Database()
        self._write_count = 0
        self._ensure_table()

    def _ensure_table(self):
        """Create the rate limit table if it doesn't exist."""
        # Database.execute handles placeholder conversion if needed
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS ratelimit_buckets (
                key TEXT PRIMARY KEY,
                bucket_type TEXT,
                tokens REAL,
                last_update REAL,
                expires_at REAL,
                data TEXT
            )
        """
        )
        # Database class handles index creation differences if any (usually similar)
        if self._db.type == "sqlite":
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ratelimit_expires ON ratelimit_buckets(expires_at)"
            )
        else:
            # PostgreSQL syntax
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_ratelimit_expires ON ratelimit_buckets(expires_at)"
            )

    def _cleanup(self):
        """Remove expired buckets."""
        now = time.time()
        # Database.execute converts ? to %s for PostgreSQL
        self._db.execute("DELETE FROM ratelimit_buckets WHERE expires_at < ?", (now,))

    def _maybe_enforce_cap(self) -> None:
        """Evict oldest rows when the bucket table exceeds its bound.

        F8: called on the write path (throttled to every 10th write plus
        an expired-row purge). Best-effort -- a cap failure must never
        break the rate-limit check itself.
        """
        try:
            self._write_count = getattr(self, "_write_count", 0) + 1
            if self._write_count % 10 != 0:
                return
            now = time.time()
            self._db.execute(
                "DELETE FROM ratelimit_buckets WHERE expires_at < ?", (now,)
            )
            row = self._db.fetch_one("SELECT COUNT(*) AS c FROM ratelimit_buckets")
            count = int(row["c"]) if row and "c" in row else 0
            if count > self.MAX_BUCKET_ROWS:
                excess = count - self.MAX_BUCKET_ROWS
                # Delete oldest-expiring rows first. (``Database.execute``
                # converts ``?`` to ``%s`` for PostgreSQL.)
                self._db.execute(
                    """DELETE FROM ratelimit_buckets WHERE key IN (
                        SELECT key FROM ratelimit_buckets
                        ORDER BY expires_at ASC LIMIT ?
                    )""",
                    (excess,),
                )
        except Exception:
            pass

    def get_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        """Get bucket state."""
        self._cleanup()
        row = self._db.fetch_one(
            "SELECT tokens, last_update, data FROM ratelimit_buckets WHERE key = ?",
            (key,),
        )
        if not row:
            return None

        try:
            data = (
                json.loads(row["data"]) if row["data"] and row["data"].strip() else {}
            )
        except (json.JSONDecodeError, TypeError):
            data = {}
        data.update({"tokens": row["tokens"], "last_update": row["last_update"]})
        return data

    def set_bucket(
        self, key: str, state: Dict[str, Any], ttl: Optional[float] = None
    ) -> None:
        """Set bucket state."""
        now = time.time()
        expires_at = now + (ttl or 86400)

        # Separate tokens and last_update from other data
        state_copy = state.copy()
        tokens = state_copy.pop("tokens", 0.0)
        last_update = state_copy.pop("last_update", now)
        bucket_type = state_copy.get("bucket_type", "route")

        data_json = json.dumps(state_copy)

        self._db.upsert(
            "ratelimit_buckets",
            ["key", "bucket_type", "tokens", "last_update", "expires_at", "data"],
            (
                key,
                bucket_type,
                float(tokens),
                float(last_update),
                float(expires_at),
                data_json,
            ),
            conflict_columns=["key"],
        )
        # F8: TTL is always set (expires_at above) and the row cap is
        # enforced on the write path.
        self._maybe_enforce_cap()

    def delete_bucket(self, key: str) -> bool:
        """Delete a bucket."""
        cursor = self._db.execute("DELETE FROM ratelimit_buckets WHERE key = ?", (key,))
        return cursor.rowcount > 0

    def get_keys_by_prefix(self, prefix: str) -> List[str]:
        """Get keys by prefix."""
        rows = self._db.fetch_all(
            "SELECT key FROM ratelimit_buckets WHERE key LIKE ?", (f"{prefix}%",)
        )
        return [row["key"] for row in rows]

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete keys by prefix."""
        cursor = self._db.execute(
            "DELETE FROM ratelimit_buckets WHERE key LIKE ?", (f"{prefix}%",)
        )
        return cursor.rowcount

    def clear_all(self) -> None:
        """Clear all buckets."""
        self._db.execute("DELETE FROM ratelimit_buckets")

    def increment(self, key: str, field: str, amount: int = 1) -> int:
        """Increment a field (non-atomic for extra fields)."""
        state = self.get_bucket(key) or {}
        val = state.get(field, 0) + amount
        state[field] = val
        self.set_bucket(key, state)
        return val

    def get_and_set(self, key: str, field: str, value: Any, default: Any = None) -> Any:
        """Get and set a field."""
        state = self.get_bucket(key) or {}
        prev = state.get(field, default)
        state[field] = value
        self.set_bucket(key, state)
        return prev

    def add_to_list(
        self, key: str, field: str, value: Any, max_size: int = 1000
    ) -> int:
        """Add to list field."""
        state = self.get_bucket(key) or {}
        lst = state.get(field, [])
        if not isinstance(lst, list):
            lst = []
        lst.append(value)
        if len(lst) > max_size:
            lst = lst[-max_size:]
        state[field] = lst
        self.set_bucket(key, state)
        return len(lst)

    def trim_list(self, key: str, field: str, min_value: Any) -> int:
        """Trim list field."""
        state = self.get_bucket(key) or {}
        lst = state.get(field, [])
        if not isinstance(lst, list):
            return 0
        original_len = len(lst)
        lst = [v for v in lst if v >= min_value]
        state[field] = lst
        self.set_bucket(key, state)
        return original_len - len(lst)

    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        """Database handles concurrency via its own locking mechanisms."""
        return f"{self._db.type}-lock"

    def release_lock(self, key: str, token: Optional[str] = None) -> None:
        """Release lock."""
        pass

    def eval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        """Atomically evaluate token bucket using a transaction.

        Transient ``database is locked`` errors (a normal SQLite occurrence
        when many requests write the bucket table concurrently) are retried a
        few times before the request is denied. This prevents a busy-but-healthy
        database from turning into spurious 429 responses.
        """
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                result = self._eval_token_bucket_once(
                    key, capacity, refill_rate, cost, ttl
                )
                # F8: bound table growth on the write path (never raises).
                self._maybe_enforce_cap()
                return result
            except Exception as exc:
                self._rollback_safely()
                if attempt + 1 < max_attempts and _is_lock_error(exc):
                    time.sleep(0.05 * (attempt + 1))
                    continue
                return self._fail_closed(exc, capacity)
        # Unreachable: the loop returns on every path above.
        return False, 0, 60.0

    def _eval_token_bucket_once(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int
    ) -> tuple:
        """Perform a single token-bucket evaluation.

        SECURITY (F6): the previous SELECT-then-UPDATE inside one
        transaction lost updates under concurrent consumers (multi-worker
        when Valkey is absent): two workers read the same ``tokens``
        value, both deducted from that stale snapshot, and the last
        writer won — a 5-token bucket admitted ~10 parallel requests.
        A follow-up compare-and-set attempt (``SET tokens = <stale-1>
        ... WHERE tokens >= cost``) did NOT fix it: the predicate only
        checks that *some* token remains, so two workers holding the
        same stale balance both land their absolute write and one
        deduction is still lost.

        The deduction is therefore a single RELATIVE statement —
        ``UPDATE ... SET tokens = tokens - ? ... WHERE key = ? AND
        tokens >= ?`` — so the database itself serializes concurrent
        spends and ``cursor.rowcount`` tells exactly whether *this*
        worker got the tokens. Refill accrual is likewise applied as a
        relative, capacity-clamped add (portable ``CASE`` instead of
        ``LEAST``/``min()``, which differ between PostgreSQL and
        SQLite) guarded by ``last_update <= ?`` so a refill computed
        from a stale read can never clobber a concurrent spend; on
        ``rowcount == 0`` we re-read and retry. Denied requests write
        nothing (refill is formula-based from ``last_update``, so no
        accrual is lost). ``?`` placeholders are converted to ``%s``
        for PostgreSQL by the Database layer, keeping this portable
        across both dialects without ``SELECT FOR UPDATE``.
        """
        max_cas_attempts = 10
        for _ in range(max_cas_attempts):
            # Fresh clock per attempt: a stale ``now`` carried across
            # retries would under-compute refill and could move
            # ``last_update`` backwards past a concurrent worker's write.
            now = time.time()
            expires_at = now + ttl

            # First, ensure entry exists
            self._db.insert_or_ignore(
                "ratelimit_buckets",
                ["key", "tokens", "last_update", "expires_at"],
                (key, float(capacity), float(now), float(expires_at)),
            )

            row = self._db.fetch_one(
                "SELECT tokens, last_update FROM ratelimit_buckets WHERE key = ?",
                (key,),
            )
            if not row:
                # Row vanished between insert and select (cleanup race):
                # treat as a fresh full bucket without spending tokens.
                return True, capacity, 0.0

            try:
                stored = float(row["tokens"])
            except (TypeError, ValueError):
                stored = float(capacity)

            if row["last_update"] is None:
                # Legacy row with no clock: normalize it under an
                # ``IS NULL`` guard (portable across SQLite/PostgreSQL)
                # so a concurrent spend that set the clock makes us
                # retry instead of clobbering it.
                refilled_legacy = min(float(capacity), stored)
                cursor = self._db.execute(
                    """
                    UPDATE ratelimit_buckets
                    SET tokens = ?, last_update = ?, expires_at = ?
                    WHERE key = ? AND last_update IS NULL
                    """,
                    (refilled_legacy, now, expires_at, key),
                )
                if cursor.rowcount == 0:
                    continue
                stored = refilled_legacy
                last_update = now
            else:
                try:
                    last_update = float(row["last_update"])
                except (TypeError, ValueError):
                    last_update = now

            elapsed = max(0.0, now - last_update)
            refilled = min(
                float(capacity),
                stored + elapsed * refill_rate,
            )

            if refilled > stored:
                # Persist accrual as a RELATIVE clamped add so a
                # concurrent relative spend is preserved (both are
                # order-independent increments), never overwritten.
                # The ``last_update <= ?`` guard makes a stale refill
                # back off (rowcount 0 -> re-read) instead of double
                # -applying accrual another worker already persisted.
                # Inequality (not equality) keeps this robust to REAL
                # float4 rounding on PostgreSQL.
                delta = refilled - stored
                cap = float(capacity)
                cursor = self._db.execute(
                    """
                    UPDATE ratelimit_buckets
                    SET tokens = CASE WHEN tokens + ? > ?
                                        THEN ? ELSE tokens + ? END,
                        last_update = ?, expires_at = ?
                    WHERE key = ? AND last_update <= ?
                    """,
                    (delta, cap, cap, delta, now, expires_at, key, last_update),
                )
                if cursor.rowcount == 0:
                    # Another worker touched the row after our read:
                    # re-read and recompute instead of spending stale.
                    continue
                row = self._db.fetch_one(
                    "SELECT tokens FROM ratelimit_buckets WHERE key = ?",
                    (key,),
                )
                if not row:
                    return True, capacity, 0.0
                stored = float(row["tokens"])

            if stored >= cost:
                # ATOMIC SPEND: relative decrement guarded by the
                # balance predicate in the SAME statement. Concurrent
                # spends serialize in the database; exactly one worker
                # wins each token and rowcount reports who.
                cursor = self._db.execute(
                    """
                    UPDATE ratelimit_buckets
                    SET tokens = tokens - ?, last_update = ?, expires_at = ?
                    WHERE key = ? AND tokens >= ?
                    """,
                    (float(cost), now, expires_at, key, float(cost)),
                )
                if cursor.rowcount == 0:
                    # Another worker spent the tokens first: re-read
                    # and retry.
                    continue
                # The spend landed (rowcount > 0): the database applied
                # exactly one ``cost`` decrement. Report remaining from
                # the balance this spend was computed against — the same
                # contract as before (a re-SELECT here would race a
                # concurrent spend and attribute *its* deduction to us).
                return True, max(0, int(stored - cost)), 0.0

            # Denied: persist nothing. Refill accrues from last_update
            # by formula, so skipping the write loses no accrual and —
            # critically — cannot overwrite a concurrent spend.
            remaining = int(stored)
            if refill_rate > 0:
                reset_after = max(0.0, (cost - stored) / refill_rate)
            else:
                reset_after = float(ttl)
            return False, remaining, reset_after

        # CAS contention did not settle after retries: fail closed.
        raise RuntimeError(f"token bucket CAS did not converge for key {key!r}")

    def _rollback_safely(self) -> None:
        """Roll back any open transaction without masking the original error."""
        try:
            self._db.rollback()
        except Exception:
            pass

    def _fail_closed(self, exc: Exception, capacity: int) -> tuple:
        """Deny the request after a non-retryable backend error."""
        # SECURITY: the previous ``except Exception: return
        # (True, capacity, 0.0)`` FAIL-OPENED the bucket on any
        # DB error. A buggy or under-attack database turned into
        # an unlimited-rate bypass. We now FAIL-CLOSED: deny the
        # request and surface the error. Operators can opt-in to
        # fail-open via configuration for dev/test environments.
        import os as _os
        import utils.config as _cfg

        try:
            fail_open = bool(
                _cfg.get("rate_limiting", {}).get("fail_open_on_backend_error", False)
            )
        except Exception:
            fail_open = False

        if fail_open and not _os.environ.get("PLEXICHAT_REQUIRE_FAIL_CLOSED", ""):
            from utils import logger as _log

            _log.error(
                "rate-limit DB backend error, FAIL-OPEN (insecure): %s",
                exc,
            )
            return True, capacity, 0.0

        from utils import logger as _log

        _log.critical(
            "rate-limit DB backend error, FAIL-CLOSED (denying request): %s",
            exc,
        )
        return False, 0, 60.0
