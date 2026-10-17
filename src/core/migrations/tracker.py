"""
Migration tracker for recording and retrieving migration history.

This module provides the MigrationTracker class for tracking applied migrations,
recording their status, and managing the migrations_history table.
"""

from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
import hashlib
import hmac
import threading
import time
import logging
import json
import re
import secrets
import os
import uuid
from . import schema
from .locking import MigrationLockingMixin

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    """Return the current UTC time as a naive datetime (SQLite-safe).

    ``datetime.utcnow()`` is deprecated since Python 3.12; this keeps the
    same naive-UTC semantics the tracker's stored values rely on.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class MigrationTracker(MigrationLockingMixin):
    """Tracks applied migrations and their status in the database."""

    # Process-wide registry: db identity -> uptime session id. ensure_table_exists()
    # is invoked by nearly every tracker/manager operation; without this guard each
    # call used to INSERT a fresh near-zero-length migration_uptime row (unbounded
    # row spam that permanently skewed the irreversible-migration uptime gate).
    _session_registry: Dict[str, int] = {}
    _session_registry_lock = threading.Lock()

    def __init__(self, db):
        """
        Initialize the migration tracker.

        Args:
            db: Database instance from plexichat.src.core.database
        """
        self.db = db
        self._start_time = time.time()
        self._current_uptime_session_id = None
        self._lock_owner: Optional[str] = None
        self._pg_advisory_conn = None

    def ensure_table_exists(self):
        """
        Ensure the migrations_history table exists.

        Creates the table if it doesn't already exist.

        Raises:
            Exception: If table creation fails
        """
        schema.create_tables(self.db)
        self.ensure_lock_table_exists()
        self._start_uptime_session()

    def _db_identity(self) -> str:
        """Return a stable identity string for the tracked database."""
        cfg = getattr(self.db, "config", None) or {}
        db_type = getattr(self.db, "type", None) or str(cfg.get("type", "unknown"))
        if db_type == "sqlite":
            path = os.path.abspath(os.path.expanduser(str(cfg.get("path", ""))))
            return f"sqlite:{path}"
        pg = cfg.get("postgres", {}) or {}
        return (
            f"postgres:{pg.get('host', '')}:{pg.get('port', '')}:{pg.get('dbname', '')}"
        )

    def _start_uptime_session(self):
        """Start a new uptime tracking session (once per process per database)."""
        key = self._db_identity()
        with self._session_registry_lock:
            existing = self._session_registry.get(key)
            if existing is not None:
                # Already started for this process/database: reuse the same
                # session row instead of inserting a duplicate.
                self._current_uptime_session_id = existing
                return
            try:
                self.db.execute(
                    "INSERT INTO migration_uptime (start_time, uptime_seconds) VALUES (?, 0)",
                    (_utcnow(),),
                )
                self._current_uptime_session_id = self.db.fetch_last_insert_id()
                self._session_registry[key] = self._current_uptime_session_id
                logger.info(
                    f"Started uptime tracking session: {self._current_uptime_session_id}"
                )
            except Exception as e:
                logger.warning(f"Failed to start uptime session: {e}")

    def update_uptime(self):
        """Update the current uptime session with elapsed time."""
        if self._current_uptime_session_id is None:
            return

        try:
            elapsed = int(time.time() - self._start_time)
            self.db.execute(
                "UPDATE migration_uptime SET uptime_seconds = ? WHERE id = ?",
                (elapsed, self._current_uptime_session_id),
            )
        except Exception as e:
            logger.warning(f"Failed to update uptime: {e}")

    @staticmethod
    def _coerce_datetime(value) -> Optional[datetime]:
        """Best-effort coercion of a stored timestamp to a naive datetime."""
        if isinstance(value, datetime):
            return value.replace(tzinfo=None) if value.tzinfo else value
        if isinstance(value, str):
            text = value.strip()
            for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue
        return None

    def _current_session_contribution(self, applied_at: datetime) -> int:
        """Extra seconds the still-open session has accrued beyond its stored
        uptime_seconds. This keeps the gate correct even when update_uptime()
        has not been called recently (it is only invoked from the admin status
        endpoint)."""
        if self._current_uptime_session_id is None:
            return 0
        try:
            row = self.db.fetch_one(
                "SELECT start_time, uptime_seconds FROM migration_uptime WHERE id = ?",
                (self._current_uptime_session_id,),
            )
            if not row:
                return 0
            start_raw = (
                row[0] if isinstance(row, (list, tuple)) else row.get("start_time")
            )
            stored_raw = (
                row[1] if isinstance(row, (list, tuple)) else row.get("uptime_seconds")
            )
            start = self._coerce_datetime(start_raw)
            if start is None or start < applied_at:
                return 0
            stored = int(stored_raw or 0)
            live = max(0, int((_utcnow() - start).total_seconds()))
            # The SUM already counted `stored`; only add the unrecorded excess.
            return max(0, max(stored, live) - stored)
        except Exception as e:
            logger.debug(f"Could not compute live session uptime contribution: {e}")
            return 0

    def get_total_uptime_since(self, since_version: str) -> int:
        """
        Get total server uptime in seconds since a specific migration was applied.

        Sums every uptime session that started at/after the migration's
        applied_at timestamp, plus the live elapsed time of the current
        still-open session so the total does not depend on update_uptime()
        having been polled.

        Args:
            since_version: Migration version to calculate uptime from

        Returns:
            Total uptime in seconds
        """
        try:
            result = self.db.fetch_one(
                "SELECT applied_at FROM migrations_history WHERE version = ? AND status = ?",
                (since_version, "completed"),
            )
            if not result:
                return 0

            applied_raw = (
                result[0]
                if isinstance(result, (list, tuple))
                else result.get("applied_at")
            )
            applied_at = self._coerce_datetime(applied_raw)
            if applied_at is None:
                logger.error(
                    f"Unparseable applied_at for migration {since_version}: {applied_raw!r}"
                )
                return 0

            # Sum uptime from all sessions that started after the migration
            result = self.db.fetch_one(
                """
                SELECT COALESCE(SUM(uptime_seconds), 0) as total_uptime
                FROM migration_uptime
                WHERE start_time >= ?
                """,
                (applied_at,),
            )

            total = (
                result[0]
                if isinstance(result, (list, tuple))
                else result.get("total_uptime", 0)
            )
            total = int(total) if total else 0
            total += self._current_session_contribution(applied_at)
            return total
        except Exception as e:
            logger.error(f"Failed to calculate uptime: {e}")
            return 0

    def get_migration_metadata(self, version: str) -> Dict[str, Any]:
        """
        Parse migration metadata from the migration file docstring.

        Args:
            version: Migration version

        Returns:
            Dictionary with metadata fields
        """
        try:
            import importlib.util
            import os

            migration_dir = os.path.dirname(os.path.abspath(__file__))
            migration_file = os.path.join(migration_dir, "migrations", f"{version}.py")

            if not os.path.exists(migration_file):
                return {}

            spec = importlib.util.spec_from_file_location(
                f"migration_{version}", migration_file
            )
            if spec is None or spec.loader is None:
                return {}

            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            docstring = module.__doc__
            if not docstring:
                return {}

            # Parse MIGRATION_METADATA from docstring
            # Look for MIGRATION_METADATA: followed by JSON object
            match = re.search(
                r"MIGRATION_METADATA:\s*\n(\{.*?\})\s*(?:\n\"\"\"|$)",
                docstring,
                re.DOTALL,
            )
            if not match:
                return {}

            try:
                metadata = json.loads(match.group(1))
                return metadata
            except json.JSONDecodeError:
                return {}
        except Exception as e:
            logger.warning(f"Failed to parse metadata for migration {version}: {e}")
            return {}

    def _latest_completed_applied_at(self) -> Optional[datetime]:
        """Return the applied_at of the most recently completed migration."""
        result = self.db.fetch_one(
            "SELECT applied_at FROM migrations_history WHERE status = ? "
            "ORDER BY id DESC LIMIT 1",
            ("completed",),
        )
        if not result:
            return None
        raw = (
            result[0] if isinstance(result, (list, tuple)) else result.get("applied_at")
        )
        return self._coerce_datetime(raw)

    def can_run_irreversible_migration(
        self, version: str, delay_hours: int = 168
    ) -> tuple[bool, str]:
        """
        Check if an irreversible migration can be run based on uptime delay.

        The uptime gate is ALWAYS enforced for irreversible migrations. If the
        migration declares depends_on, uptime is measured since its last
        dependency; otherwise it is measured since the most recent completed
        migration (a missing depends_on must not bypass the delay).

        Args:
            version: Migration version to check
            delay_hours: Required uptime delay in hours (default 7 days)

        Returns:
            Tuple of (can_run, reason)
        """
        metadata = self.get_migration_metadata(version)
        if not metadata.get("irreversible"):
            return True, "Migration is reversible"

        # Check for emergency override (single-use; CHECKED here but only
        # CLAIMED at execution time, so read-only status views and gate
        # pre-checks never burn the token).
        override_token = os.environ.get("EMERGENCY_MIGRATION_OVERRIDE")
        if override_token:
            if self._peek_emergency_override(override_token):
                return True, self.EMERGENCY_OVERRIDE_ACTIVE_REASON
            else:
                return False, "Invalid emergency override token"

        # Check dependencies (ordering only - never a substitute for the gate)
        depends_on = metadata.get("depends_on", []) or []
        for dep in depends_on:
            dep_status = self.get_migration_status(dep)
            if not dep_status or dep_status.get("status") != "completed":
                return False, f"Dependency {dep} not completed"

        # Uptime-delay gate: unconditional for irreversible migrations.
        if depends_on:
            uptime_seconds = self.get_total_uptime_since(depends_on[-1])
        else:
            reference = self._latest_completed_applied_at()
            if reference is None:
                return (
                    False,
                    "Irreversible migration requires completed migrations and "
                    "recorded uptime; no completed migrations found",
                )
            try:
                result = self.db.fetch_one(
                    """
                    SELECT COALESCE(SUM(uptime_seconds), 0) as total_uptime
                    FROM migration_uptime
                    WHERE start_time >= ?
                    """,
                    (reference,),
                )
                total = (
                    result[0]
                    if isinstance(result, (list, tuple))
                    else result.get("total_uptime", 0)
                )
                uptime_seconds = int(total or 0)
                uptime_seconds += self._current_session_contribution(reference)
            except Exception as e:
                logger.error(f"Failed to calculate uptime gate: {e}")
                return False, "Could not evaluate uptime gate"

        required_seconds = delay_hours * 3600
        if uptime_seconds < required_seconds:
            remaining_hours = (required_seconds - uptime_seconds) / 3600
            return (
                False,
                f"Insufficient uptime. Required: {delay_hours}h, "
                f"Current: {uptime_seconds / 3600:.1f}h, "
                f"Remaining: {remaining_hours:.1f}h",
            )

        return True, "All checks passed"

    @staticmethod
    def _hash_token(token: str) -> str:
        """Hash an emergency override token for storage/lookup (SHA-256 hex)."""
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    #: Sentinel for dummy constant-time comparisons so rejection timing does
    #: not reveal whether a token hash exists in the table.
    _TOKEN_MISS_SENTINEL = "0" * 64

    #: Gate reason returned when the irreversible check passes via override.
    #: ``MigrationManager`` matches on this to claim the token exactly once
    #: before executing (fail closed if it was raced/expired since the peek).
    EMERGENCY_OVERRIDE_ACTIVE_REASON = "Emergency override active"

    def peek_emergency_override_from_env(self) -> bool:
        """Check the EMERGENCY_MIGRATION_OVERRIDE env token WITHOUT consuming.

        Read-only gate/status paths must use this: peeking never burns the
        strictly single-use token.
        """
        token = os.environ.get("EMERGENCY_MIGRATION_OVERRIDE")
        if not token:
            return False
        return self._peek_emergency_override(token)

    def _peek_emergency_override(self, token: str) -> bool:
        """
        Non-consuming validity check for an emergency override token.

        The candidate is hashed first (plaintext secrets are never compared);
        the stored digest is then verified with ``hmac.compare_digest``
        (constant-time) and the row must be unused and unexpired. Fails
        closed on any error or unparseable state.
        """
        try:
            if not token:
                return False
            candidate = self._hash_token(token)
            row = self.db.fetch_one(
                "SELECT token, used_at, expires_at "
                "FROM emergency_override_tokens WHERE token = ?",
                (candidate,),
            )
            if row is None:
                # Normalize timing so missing hashes are indistinguishable.
                hmac.compare_digest(candidate, self._TOKEN_MISS_SENTINEL)
                return False
            if isinstance(row, (list, tuple)):
                stored_hash = row[0] if len(row) > 0 else None
                used_at = row[1] if len(row) > 1 else None
                expires_at = row[2] if len(row) > 2 else None
            else:
                stored_hash = row.get("token")
                used_at = row.get("used_at")
                expires_at = row.get("expires_at")
            if not isinstance(stored_hash, str) or not hmac.compare_digest(
                candidate, stored_hash
            ):
                return False
            if used_at is not None:
                return False
            expires = self._coerce_datetime(expires_at)
            if expires is None:
                return False
            return expires > _utcnow()
        except Exception as e:
            logger.error(f"Failed to check emergency override: {e}")
            return False

    def try_consume_emergency_override_from_env(self) -> bool:
        """Consume the EMERGENCY_MIGRATION_OVERRIDE env token, if present.

        Returns True only when a valid, previously-unused token was present in
        the environment AND successfully claimed (strictly single-use).
        """
        token = os.environ.get("EMERGENCY_MIGRATION_OVERRIDE")
        if not token:
            return False
        return self._validate_emergency_override(token)

    def _validate_emergency_override(self, token: str) -> bool:
        """
        Validate an emergency override token, consuming it on first use.

        Tokens are strictly single-use: the first successful validation
        atomically marks the row as used so a leaked token can never be
        replayed. Tokens are persisted only as SHA-256 digests, so the claim
        UPDATE matches on the digest - plaintext secrets are never compared,
        and the fallback ownership check uses ``hmac.compare_digest``
        (constant-time).

        Args:
            token: Token to validate

        Returns:
            True if the token was valid and successfully claimed
        """
        try:
            candidate = self._hash_token(token)
            now = _utcnow()
            claim = f"validation:{uuid.uuid4().hex}"
            # Atomic single-use claim: exactly one caller's UPDATE matches an
            # unconsumed, unexpired token row (verified via rowcount).
            cursor = self.db.execute(
                """
                UPDATE emergency_override_tokens
                SET used_at = ?, used_by = ?
                WHERE token = ? AND used_at IS NULL AND expires_at > ?
                """,
                (now, claim, candidate, now),
            )

            rowcount = getattr(cursor, "rowcount", None)
            if isinstance(rowcount, int) and rowcount >= 0:
                if rowcount > 0:
                    logger.warning(
                        "Emergency override token validated "
                        "(single-use consumed; tokens stored hashed)"
                    )
                    return True
                # Normalize rejection timing so invalid/expired/used tokens
                # are indistinguishable from one another by response time.
                hmac.compare_digest(candidate, self._TOKEN_MISS_SENTINEL)
                logger.warning("Emergency override token rejected (invalid/expired)")
                return False

            # Fallback for drivers without cursor.rowcount: confirm our unique
            # claim marker won the update (constant-time comparison).
            row = self.db.fetch_one(
                "SELECT used_by FROM emergency_override_tokens WHERE token = ?",
                (candidate,),
            )
            stored_claim = None
            if row is not None:
                stored_claim = (
                    row.get("used_by")
                    if hasattr(row, "get")
                    else row[0]
                    if isinstance(row, (list, tuple)) and row
                    else None
                )
            claimed_by_us = isinstance(stored_claim, str) and hmac.compare_digest(
                claim, stored_claim
            )
            if claimed_by_us:
                logger.warning(
                    "Emergency override token validated "
                    "(single-use consumed; tokens stored hashed)"
                )
            else:
                logger.warning("Emergency override token rejected (invalid/expired)")
            return claimed_by_us
        except Exception as e:
            logger.error(f"Failed to validate emergency override: {e}")
            return False

    def generate_emergency_override(
        self, reason: str, expires_minutes: int = 30
    ) -> str:
        """
        Generate an emergency override token.

        Only the SHA-256 hash of the token is persisted; the plaintext value is
        returned exactly once to the caller.

        Args:
            reason: Reason for the override
            expires_minutes: Token expiration time in minutes

        Returns:
            Generated token
        """
        token = secrets.token_hex(32)
        token_hash = self._hash_token(token)
        expires_at = _utcnow() + timedelta(minutes=expires_minutes)

        try:
            self.db.execute(
                """
                INSERT INTO emergency_override_tokens (token, created_at, expires_at, reason)
                VALUES (?, ?, ?, ?)
                """,
                (token_hash, _utcnow(), expires_at, reason),
            )
            logger.info(f"Generated emergency override token (expires: {expires_at})")
            return token
        except Exception as e:
            logger.error(f"Failed to generate emergency override: {e}")
            raise

    def use_emergency_override(self, token: str, used_by: str):
        """
        Mark an emergency override token as used.

        Args:
            token: Token to mark as used (plaintext; matched against stored hash)
            used_by: User/admin who used the token
        """
        try:
            cursor = self.db.execute(
                """
                UPDATE emergency_override_tokens
                SET used_at = ?, used_by = ?
                WHERE token = ? AND used_at IS NULL
                """,
                (_utcnow(), used_by, self._hash_token(token)),
            )
            rowcount = getattr(cursor, "rowcount", 0)
            if rowcount == 0:
                logger.warning(
                    f"Emergency override use by {used_by} had no effect "
                    "(unknown or already-consumed token)"
                )
            else:
                logger.warning(f"Emergency override token used by {used_by}")
        except Exception as e:
            logger.error(f"Failed to mark emergency override as used: {e}")

    def log_migration(self, version: str, level: str, message: str):
        """
        Log a migration message to the database.

        Args:
            version: Migration version
            level: Log level (INFO, WARNING, ERROR)
            message: Log message
        """
        try:
            self.db.execute(
                """
                INSERT INTO migration_logs (migration_version, level, message, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                (version, level, message, _utcnow()),
            )
        except Exception as e:
            logger.warning(f"Failed to log migration message: {e}")

    def get_migration_logs(self, version: str) -> List[Dict[str, Any]]:
        """
        Get all logs for a specific migration.

        Args:
            version: Migration version

        Returns:
            List of log entries
        """
        try:
            result = self.db.fetch_all(
                """
                SELECT level, message, timestamp
                FROM migration_logs
                WHERE migration_version = ?
                ORDER BY timestamp ASC
                """,
                (version,),
            )

            if not result:
                return []

            logs = []
            for row in result:
                if isinstance(row, (list, tuple)):
                    logs.append(
                        {"level": row[0], "message": row[1], "timestamp": row[2]}
                    )
                else:
                    logs.append(dict(row))

            return logs
        except Exception as e:
            logger.error(f"Failed to get migration logs: {e}")
            return []

    def get_applied_migrations(self) -> List[str]:
        """
        Get list of applied migration versions.

        Returns:
            List of version strings (e.g., ['001', '002', '003'])

        Raises:
            Exception: If database query fails
        """
        self.ensure_table_exists()

        result = self.db.fetch_all(
            "SELECT version FROM migrations_history WHERE status = ? ORDER BY version",
            ("completed",),
        )

        return (
            [
                row[0] if isinstance(row, (list, tuple)) else row["version"]
                for row in result
            ]
            if result
            else []
        )

    def record_migration_start(self, version: str, name: str, checksum: str):
        """
        Record the start of a migration.

        Handles both initial migrations and retries of failed migrations by using
        upsert to avoid UNIQUE constraint violations.

        Args:
            version: Migration version (e.g., '001')
            name: Migration name/description
            checksum: SHA256 checksum of migration file

        Raises:
            Exception: If database insert/update fails
        """
        self.ensure_table_exists()

        # Get migration metadata
        metadata = self.get_migration_metadata(version)
        is_irreversible = metadata.get("irreversible", False)
        depends_on = (
            json.dumps(metadata.get("depends_on", []))
            if metadata.get("depends_on")
            else None
        )
        metadata_json = json.dumps(metadata) if metadata else None

        # Use upsert to handle both new migrations and retries of failed ones
        # Get existing created_at if it exists to preserve it
        existing = self.get_migration_status(version)
        created_at = existing.get("created_at") if existing else _utcnow()

        self.db.upsert(
            "migrations_history",
            [
                "version",
                "name",
                "checksum",
                "status",
                "applied_by",
                "created_at",
                "updated_at",
                "is_irreversible",
                "depends_on",
                "metadata",
            ],
            (
                version,
                name,
                checksum,
                "running",
                "system",
                created_at,
                _utcnow(),
                is_irreversible,
                depends_on,
                metadata_json,
            ),
            conflict_columns=["version"],
        )

    def record_migration_success(
        self, version: str, execution_time_ms: int, rollback_sql: Optional[str] = None
    ):
        """
        Record successful migration completion.

        Args:
            version: Migration version
            execution_time_ms: Execution time in milliseconds
            rollback_sql: Optional SQL for rolling back the migration

        Raises:
            Exception: If database update fails
        """
        self.ensure_table_exists()

        self.db.execute(
            """
            UPDATE migrations_history 
            SET status = ?, execution_time_ms = ?, rollback_sql = ?, updated_at = ?
            WHERE version = ?
            """,
            ("completed", execution_time_ms, rollback_sql, _utcnow(), version),
        )

    def record_migration_failure(self, version: str, error_message: str):
        """
        Record migration failure.

        Args:
            version: Migration version
            error_message: Error message describing the failure

        Raises:
            Exception: If database update fails
        """
        self.ensure_table_exists()

        self.db.execute(
            """
            UPDATE migrations_history 
            SET status = ?, error_message = ?, updated_at = ?
            WHERE version = ?
            """,
            ("failed", error_message, _utcnow(), version),
        )

    def get_migration_status(self, version: str) -> Optional[Dict[str, Any]]:
        """
        Get status of a specific migration.

        Args:
            version: Migration version

        Returns:
            Dictionary with migration details or None if not found

        Raises:
            Exception: If database query fails
        """
        self.ensure_table_exists()

        result = self.db.fetch_all(
            "SELECT * FROM migrations_history WHERE version = ?", (version,)
        )

        if not result:
            return None

        row = result[0]

        # Handle both tuple and dict-like row formats
        if isinstance(row, (list, tuple)):
            return {
                "id": row[0],
                "version": row[1],
                "name": row[2],
                "applied_at": row[3],
                "applied_by": row[4],
                "execution_time_ms": row[5],
                "checksum": row[6],
                "status": row[7],
                "rollback_sql": row[8],
                "error_message": row[9],
            }
        else:
            return dict(row)

    def get_all_migration_records(self) -> List[Dict[str, Any]]:
        """
        Get all migration records ordered by application time.

        Returns:
            List of migration record dictionaries

        Raises:
            Exception: If database query fails
        """
        self.ensure_table_exists()

        result = self.db.fetch_all(
            "SELECT * FROM migrations_history ORDER BY applied_at ASC"
        )

        if not result:
            return []

        records = []
        for row in result:
            if isinstance(row, (list, tuple)):
                records.append(
                    {
                        "id": row[0],
                        "version": row[1],
                        "name": row[2],
                        "applied_at": row[3],
                        "applied_by": row[4],
                        "execution_time_ms": row[5],
                        "checksum": row[6],
                        "status": row[7],
                        "rollback_sql": row[8],
                        "error_message": row[9],
                    }
                )
            else:
                records.append(dict(row))

        return records

    def record_rollback(self, version: str):
        """
        Record a migration rollback.

        Args:
            version: Migration version being rolled back

        Raises:
            Exception: If database update fails
        """
        self.ensure_table_exists()

        self.db.execute(
            """
            UPDATE migrations_history 
            SET status = ?, updated_at = ?
            WHERE version = ?
            """,
            ("rolled_back", _utcnow(), version),
        )

    def acquire_lock(self, timeout: int = 300) -> bool:
        """Acquire the migration lock. See ``locking.MigrationLockingMixin``."""
        return super().acquire_lock(timeout)

    def release_lock(self):
        """Release the migration lock. See ``locking.MigrationLockingMixin``."""
        return super().release_lock()

    def ensure_lock_table_exists(self):
        """Ensure the SQLite lock table exists."""
        return super().ensure_lock_table_exists()
