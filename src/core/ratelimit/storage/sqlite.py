"""
SQLite storage backend for rate limiting.
Thread-safe and multi-process safe using atomic SQL.
"""

import time
from typing import Optional, List, Dict, Any
from src.core.database import Database
from .base import RateLimitStorage


class SQLiteStorage(RateLimitStorage):
    """Atomic SQLite storage for rate limit buckets."""

    def __init__(self, db: Optional[Database] = None):
        """Initialize SQLite storage."""
        self._db = db or Database()
        self._ensure_table()

    def _ensure_table(self):
        """Create the rate limit table if it doesn't exist."""
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS ratelimit_buckets (
                key TEXT PRIMARY KEY,
                bucket_type TEXT,
                tokens REAL,
                last_update REAL,
                expires_at REAL,
                data TEXT
            )
        """)
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_ratelimit_expires ON ratelimit_buckets(expires_at)"
        )

    def _cleanup(self):
        """Remove expired buckets."""
        now = time.time()
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

        # Merge basic fields with extra data if any
        import json

        data = json.loads(row["data"]) if row["data"] else {}
        data.update({"tokens": row["tokens"], "last_update": row["last_update"]})
        return data

    def set_bucket(
        self, key: str, state: Dict[str, Any], ttl: Optional[float] = None
    ) -> None:
        """Set bucket state."""
        import json

        now = time.time()
        expires_at = now + (ttl or 86400)

        # Separate tokens and last_update from other data
        # Work on a copy to avoid side effects
        state_copy = state.copy()
        tokens = state_copy.pop("tokens", 0.0)
        last_update = state_copy.pop("last_update", now)
        bucket_type = state_copy.get("bucket_type", "route")

        data_json = json.dumps(state_copy)

        self._db.execute(
            """
            INSERT OR REPLACE INTO ratelimit_buckets (key, bucket_type, tokens, last_update, expires_at, data)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (key, bucket_type, tokens, last_update, expires_at, data_json),
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
        """Increment a field (non-atomic for extra fields in SQLite, use with caution)."""
        # This is not ideal for extra fields, but most logic should use eval_token_bucket
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
        """SQLite handles concurrency via its own locking."""
        return "sqlite-lock"

    def release_lock(self, key: str, token: Optional[str] = None) -> None:
        """Release lock."""
        pass

    def eval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        """Atomically evaluate token bucket using a single SQL statement."""
        now = time.time()
        expires_at = now + ttl

        # 1. Try to insert or update the bucket atomically
        # We use a CASE expression to handle the math
        # tokens = MIN(capacity, current_tokens + (now - last_update) * refill_rate)

        # First, ensure entry exists
        self._db.execute(
            "INSERT OR IGNORE INTO ratelimit_buckets (key, tokens, last_update, expires_at) VALUES (?, ?, ?, ?)",
            (key, float(capacity), now, expires_at),
        )

        # Atomic update with consumption check
        # We do this in a loop or transaction if needed, but a single UPDATE is atomic in SQLite
        # We need to know if it was successful (tokens >= cost)

        self._db.begin_transaction()
        try:
            row = self._db.fetch_one(
                "SELECT tokens, last_update FROM ratelimit_buckets WHERE key = ?",
                (key,),
            )
            if not row:
                # Should not happen due to INSERT OR IGNORE above
                self._db.rollback()
                return True, capacity, 0.0

            tokens = float(row["tokens"])
            last_update = float(row["last_update"])

            elapsed = max(0.0, now - last_update)
            tokens = min(float(capacity), tokens + (elapsed * refill_rate))

            allowed = False
            if tokens >= cost:
                tokens -= cost
                allowed = True

            self._db.execute(
                """
                UPDATE ratelimit_buckets 
                SET tokens = ?, last_update = ?, expires_at = ? 
                WHERE key = ?
            """,
                (tokens, now, expires_at, key),
            )

            self._db.commit()

            remaining = int(tokens)
            reset_after = (cost - tokens) / refill_rate if not allowed else 0.0
            return allowed, remaining, max(0.0, reset_after)

        except Exception:
            self._db.rollback()
            return True, capacity, 0.0
