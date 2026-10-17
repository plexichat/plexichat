"""
Migration locking primitives.

Extracted from tracker.py to keep modules small. Provides
``MigrationLockingMixin`` with atomic lock takeover (SQLite), advisory locks
on a dedicated pinned connection (PostgreSQL), and ownership-validated
release. ``MigrationTracker`` mixes this in so the public surface
(``acquire_lock`` / ``release_lock`` / ``ensure_lock_table_exists``) stays on
the tracker exactly as before.
"""

from datetime import timedelta
import logging
import os
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)


class MigrationLockingMixin:
    """Cross-process migration locking shared by all tracker instances.

    ``db`` and ``ensure_table_exists()`` are provided by the host
    ``MigrationTracker``; they are declared here so the mixin is
    type-checkable in isolation.
    """

    MIGRATION_LOCK_KEY = 12345  # Arbitrary but consistent key for migration lock
    LOCK_STALE_SECONDS = 600  # 10 minutes

    db: Any
    _pg_advisory_conn: Any

    def ensure_table_exists(self) -> None:
        raise NotImplementedError

    @staticmethod
    def _utcnow():
        # Defer to the tracker's clock helper when mixed in; kept local so this
        # module has no import cycle with tracker.py.
        from .tracker import _utcnow

        return _utcnow()

    @staticmethod
    def _make_lock_owner() -> str:
        host = os.uname().nodename if hasattr(os, "uname") else "host"
        return f"{host}:{os.getpid()}:{uuid.uuid4().hex}"

    def _get_pg_advisory_connection(self):
        """Return the dedicated pinned connection used for advisory locks.

        Session-level advisory locks live on the connection that took them;
        routing acquire and release through the pool can hand them different
        sessions (the unlock then lands on the wrong session and the lock is
        held until that connection dies). All advisory-lock statements
        therefore run on one dedicated connection owned by this tracker.
        """
        conn = self._pg_advisory_conn
        if conn is not None and getattr(conn, "closed", 1) == 0:
            return conn
        engine = getattr(self.db, "engine", None)
        if engine is None:
            raise RuntimeError("Database engine is not connected; cannot take lock")
        self._pg_advisory_conn = engine.connect()
        return self._pg_advisory_conn

    def acquire_lock(self, timeout: int = 300) -> bool:
        """
        Acquire a migration lock to prevent concurrent migration runs.

        Uses a database-level advisory lock (PostgreSQL, taken on a dedicated
        pinned connection) or a lock table (SQLite). Stale-lock takeover on
        SQLite is a single atomic conditional UPDATE verified by rowcount, so
        two waiting processes can never both observe staleness and proceed.
        The winner's identity is recorded and re-validated at release time.

        Args:
            timeout: Maximum time to wait for lock in seconds

        Returns:
            True if lock was acquired, False otherwise

        Raises:
            Exception: If lock acquisition fails
        """
        self.ensure_table_exists()

        lock_key = self.MIGRATION_LOCK_KEY
        start_time = time.time()
        owner = self._make_lock_owner()

        while time.time() - start_time < timeout:
            if self.db.type == "postgres":
                # PostgreSQL advisory lock on the dedicated pinned connection
                try:
                    conn = self._get_pg_advisory_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT pg_try_advisory_lock(%s)", (lock_key,))
                    row = cur.fetchone()
                    cur.close()
                    conn.commit()
                    value = None
                    if row:
                        value = (
                            list(row.values())[0] if isinstance(row, dict) else row[0]
                        )
                    if value:
                        self._lock_owner = owner
                        logger.info(
                            f"Migration lock acquired "
                            f"(PostgreSQL advisory lock, owner={owner})"
                        )
                        return True
                except Exception as e:
                    logger.warning(f"Advisory lock attempt failed: {e}")
            else:
                # SQLite: lock-table approach with ownership recording.
                # 1) Fast path: INSERT wins only if no row exists (PK conflict
                #    otherwise), so concurrent inserts cannot both succeed.
                try:
                    self.db.execute(
                        "INSERT INTO migration_lock (lock_key, locked_at, locked_by) VALUES (?, ?, ?)",
                        (lock_key, self._utcnow(), owner),
                    )
                    self._lock_owner = owner
                    logger.info(
                        f"Migration lock acquired (SQLite lock table, owner={owner})"
                    )
                    return True
                except Exception:
                    pass

                # 2) Atomic stale-lock takeover: a single conditional UPDATE;
                #    exactly one waiter's UPDATE matches (rowcount == 1).
                stale_cutoff = self._utcnow() - timedelta(
                    seconds=self.LOCK_STALE_SECONDS
                )
                try:
                    cursor = self.db.execute(
                        "UPDATE migration_lock SET locked_at = ?, locked_by = ? "
                        "WHERE lock_key = ? AND locked_at <= ?",
                        (self._utcnow(), owner, lock_key, stale_cutoff),
                    )
                    rowcount = getattr(cursor, "rowcount", 0)
                    if isinstance(rowcount, int) and rowcount == 1:
                        self._lock_owner = owner
                        logger.warning(
                            f"Stale migration lock taken over atomically "
                            f"(idle > {self.LOCK_STALE_SECONDS}s, new owner={owner})"
                        )
                        return True
                except Exception as e:
                    logger.debug(f"Stale-lock takeover attempt failed: {e}")

            # Wait a bit before retrying
            time.sleep(0.5)

        logger.error(f"Failed to acquire migration lock after {timeout} seconds")
        return False

    def release_lock(self):
        """
        Release the migration lock.

        Only the process that recorded ownership of the lock can release it;
        an unconditional delete by any caller would let one worker release
        another worker's active lock.

        Raises:
            Exception: If lock release fails
        """
        self.ensure_table_exists()

        lock_key = self.MIGRATION_LOCK_KEY
        owner = self._lock_owner

        if not owner:
            logger.error(
                "release_lock called without owning the migration lock; ignoring"
            )
            return

        if self.db.type == "postgres":
            # PostgreSQL advisory lock: unlock on the SAME pinned connection
            # that acquired it, then close it.
            try:
                conn = self._pg_advisory_conn
                if conn is None or getattr(conn, "closed", 1) != 0:
                    logger.error(
                        "Pinned advisory connection is gone; lock releases when "
                        "its session dies"
                    )
                    return
                cur = conn.cursor()
                cur.execute("SELECT pg_advisory_unlock(%s)", (lock_key,))
                cur.fetchone()
                cur.close()
                conn.commit()
                logger.info("Migration lock released (PostgreSQL advisory lock)")
            finally:
                if self._pg_advisory_conn is not None:
                    try:
                        self._pg_advisory_conn.close()
                    except Exception:
                        pass
                    self._pg_advisory_conn = None
        else:
            # SQLite: delete ONLY our own lock row (ownership-validated)
            cursor = self.db.execute(
                "DELETE FROM migration_lock WHERE lock_key = ? AND locked_by = ?",
                (lock_key, owner),
            )
            rowcount = getattr(cursor, "rowcount", 0)
            if isinstance(rowcount, int) and rowcount == 0:
                logger.error(
                    "Refusing/failing to release migration lock owned by "
                    "another worker (our owner id no longer matches)"
                )
                return
            logger.info("Migration lock released (SQLite lock table)")

        self._lock_owner = None

    def ensure_lock_table_exists(self):
        """
        Ensure the migration_lock table exists for SQLite.

        This table is used for cross-process locking on SQLite.
        """
        if self.db.type == "sqlite":
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS migration_lock (
                    lock_key INTEGER PRIMARY KEY,
                    locked_at TIMESTAMP NOT NULL,
                    locked_by TEXT NOT NULL
                )
            """)
