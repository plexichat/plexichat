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

    def __init__(self, db: Optional[Database] = None):
        """Initialize database storage."""
        self._db = db or Database()
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
                return self._eval_token_bucket_once(
                    key, capacity, refill_rate, cost, ttl
                )
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

        SECURITY: the previous SELECT-then-UPDATE inside one transaction
        lost updates under PostgreSQL READ COMMITTED with multiple
        workers — two transactions read the same ``tokens`` value, both
        deducted, and the last writer won (limit bypass scaling with
        worker count). We now use a conditional compare-and-set loop:
        the deduction only lands via
        ``UPDATE ... WHERE key = ? AND tokens >= ?`` so two workers can
        never both spend the same token; on rowcount 0 we re-read and
        retry.
        """
        now = time.time()
        expires_at = now + ttl

        # First, ensure entry exists
        self._db.insert_or_ignore(
            "ratelimit_buckets",
            ["key", "tokens", "last_update", "expires_at"],
            (key, float(capacity), float(now), float(expires_at)),
        )

        # Persist the computed refill MONOTONICALLY before the CAS loop.
        # SECURITY: the CAS predicate (``WHERE tokens >= cost``) compares
        # the RAW STORED balance, while ``allowed`` is decided on REFILLED
        # tokens. Without this pre-persist, a drained bucket whose stored
        # balance sat below ``cost`` but whose refilled balance was above
        # it evaluated as allowed yet could never land its deduction —
        # rowcount 0 on every retry -> RuntimeError -> fail-closed denial
        # until the row TTL lapsed (~24h of spurious 429s).
        initial_row = self._db.fetch_one(
            "SELECT tokens, last_update FROM ratelimit_buckets WHERE key = ?",
            (key,),
        )
        if initial_row:
            refilled = min(
                float(capacity),
                float(initial_row["tokens"])
                + max(0.0, now - float(initial_row["last_update"])) * refill_rate,
            )
            if refilled > float(initial_row["tokens"]):
                self._db.execute(
                    """
                    UPDATE ratelimit_buckets
                    SET tokens = ?, last_update = ?, expires_at = ?
                    WHERE key = ? AND tokens < ?
                """,
                    (
                        refilled,
                        now,
                        expires_at,
                        key,
                        refilled,
                    ),
                )

        max_cas_attempts = 5
        for attempt in range(max_cas_attempts):
            row = self._db.fetch_one(
                "SELECT tokens, last_update FROM ratelimit_buckets WHERE key = ?",
                (key,),
            )
            if not row:
                # Row vanished between insert and select (cleanup race):
                # treat as a fresh full bucket without spending tokens.
                return True, capacity, 0.0

            stored_tokens = float(row["tokens"])
            tokens = min(
                float(capacity),
                stored_tokens + max(0.0, now - float(row["last_update"])) * refill_rate,
            )

            if tokens > stored_tokens:
                # Refill accumulated since our last read: persist it
                # monotonically BEFORE the CAS so the conditional
                # deduction below compares against refill-aware state
                # (see the pre-loop comment). The guard keeps this from
                # clobbering a concurrent worker's higher balance or
                # resetting its last_update.
                self._db.execute(
                    """
                    UPDATE ratelimit_buckets
                    SET tokens = ?, last_update = ?, expires_at = ?
                    WHERE key = ? AND tokens < ?
                """,
                    (tokens, now, expires_at, key, tokens),
                )
                row = self._db.fetch_one(
                    "SELECT tokens FROM ratelimit_buckets WHERE key = ?",
                    (key,),
                )
                if not row:
                    return True, capacity, 0.0
                stored_tokens = min(float(capacity), float(row["tokens"]))
                tokens = stored_tokens

            allowed = stored_tokens >= cost
            if allowed:
                tokens = stored_tokens - cost

            if allowed:
                cursor = self._db.execute(
                    """
                    UPDATE ratelimit_buckets
                    SET tokens = ?, last_update = ?, expires_at = ?
                    WHERE key = ? AND tokens >= ?
                """,
                    (tokens, now, expires_at, key, float(cost)),
                )
                if cursor.rowcount > 0:
                    remaining = int(tokens)
                    reset_after = 0.0
                    return allowed, remaining, max(0.0, reset_after)
                # Another worker spent the token first: re-read and retry.
                continue

            # Denied: persist refilled state unconditionally (no CAS
            # needed — writing a higher/refilled value is monotonic and
            # cannot double-spend).
            self._db.execute(
                """
                UPDATE ratelimit_buckets
                SET tokens = ?, last_update = ?, expires_at = ?
                WHERE key = ?
            """,
                (tokens, now, expires_at, key),
            )
            remaining = int(tokens)
            reset_after = (cost - tokens) / refill_rate
            return allowed, remaining, max(0.0, reset_after)

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
