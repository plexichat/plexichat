"""
Artifact retention - background cleanup of expired artifact rows.

Implements the minimal, real retention behavior referenced by the admin REST
endpoint: delete every artifact whose ``expires_at`` is set and already in the
past. A scheduled background job (``RetentionCleanupJob``) runs this on a
configured interval, applying the per-server retention window to artifacts that
have a retention policy but no ``expires_at`` yet, then purging the expired
rows. Media/linked-row cascade cleanup is intentionally out of scope here
(matching the manager's `delete` semantics) and is handled by later groups.
"""

import json
import threading
import time
from typing import Any, Dict, Optional

import utils.config as config_utils
import utils.logger as logger


def purge_expired(db: Any, config: Optional[Dict[str, Any]] = None) -> int:
    """Delete artifacts whose retention window has elapsed.

    Args:
        db: A connected database instance.
        config: Optional artifacts config (unused by the current minimal
            implementation but accepted for API symmetry).

    Returns:
        The number of artifact rows removed.
    """
    if db is None:
        return 0
    try:
        now = _now_ms()
        cursor = db.execute(
            "DELETE FROM artifacts WHERE expires_at IS NOT NULL AND expires_at <= ?",
            (now,),
        )
        removed = int(getattr(cursor, "rowcount", 0) or 0)
        if removed:
            logger.info(f"Purged {removed} expired artifact(s)")
        return removed
    except Exception as e:  # pragma: no cover - defensive
        logger.error(f"Failed to purge expired artifacts: {e}", exc_info=True)
        return 0


def _now_ms() -> int:
    """Current epoch timestamp in milliseconds."""
    return int(time.time() * 1000)


def _get_artifacts_config(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Resolve the artifacts config dict, falling back to the live config."""
    if config:
        return config
    try:
        return config_utils.get("artifacts", {}) or {}
    except Exception:
        return {}


def resolve_retention_days(
    server_id: Any, config: Optional[Dict[str, Any]] = None
) -> Optional[int]:
    """Resolve the effective retention period (days) for a server.

    Priority:
    1. A per-server override from the ``server_artifact_settings`` table, when
       ``allow_per_server_override`` is enabled and a row exists for the server.
    2. The global ``default_retention_days`` (``None`` => never expire).

    Args:
        server_id: The server whose retention period is being resolved. ``None``
            falls back directly to the global default.
        config: Optional artifacts config dict.

    Returns:
        The retention period in days, or ``None`` to indicate "never expire".
    """
    artifacts_cfg = _get_artifacts_config(config)
    allow_override = bool(artifacts_cfg.get("allow_per_server_override", False))

    if server_id is not None and allow_override:
        db = _resolve_db(config)
        if db is not None:
            try:
                row = db.fetch_one(
                    "SELECT retention_days FROM server_artifact_settings "
                    "WHERE server_id = ?",
                    (server_id,),
                )
                if row is not None:
                    raw = row.get("retention_days")
                    if raw is None:
                        return None
                    try:
                        days = int(raw)
                    except (TypeError, ValueError):
                        days = None
                    if days is not None and days > 0:
                        return days
            except Exception as e:
                logger.warning(
                    f"resolve_retention_days: failed to read server override "
                    f"for server {server_id}: {e}"
                )

    default_days = artifacts_cfg.get("default_retention_days")
    if default_days is None:
        return None
    try:
        days = int(default_days)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return days


def _resolve_db(config: Optional[Dict[str, Any]] = None) -> Any:
    """Best-effort acquisition of a connected db for resolving overrides.

    The scheduled job passes the db instance it was constructed with, but
    ``resolve_retention_days`` is also callable from contexts where only a
    config is available. In that case we try the live API module as a fallback
    so the resolution still works without fatally erroring.
    """
    try:
        import src.api as api_mod

        db = api_mod.get_db()
        if db is not None:
            return db
    except Exception:
        pass
    return None


def _seconds_per_day() -> int:
    return 86400


def _parse_policy_days(policy: Any) -> Optional[int]:
    """Extract a ``days`` value from a stored ``retention_policy`` column."""
    if policy is None:
        return None
    if isinstance(policy, (int, float)):
        try:
            days = int(policy)
        except (TypeError, ValueError):
            return None
        return days if days > 0 else None
    if isinstance(policy, str):
        text = policy.strip()
        if not text:
            return None
        try:
            obj = json.loads(text)
        except Exception:
            obj = None
        if isinstance(obj, dict):
            raw = obj.get("days")
        else:
            try:
                raw = int(text)
            except (TypeError, ValueError):
                raw = None
        if raw is None:
            return None
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return None
        return days if days > 0 else None
    if isinstance(policy, dict):
        raw = policy.get("days")
        if raw is None:
            return None
        try:
            days = int(raw)
        except (TypeError, ValueError):
            return None
        return days if days > 0 else None
    return None


def _apply_retention_windows(db: Any, config: Optional[Dict[str, Any]] = None) -> int:
    """Apply retention windows to artifacts that have none yet.

    For every artifact that has a ``retention_policy`` but a ``NULL expires_at``,
    compute ``expires_at = created_at + retention_days`` using the resolved
    retention period (per-server override or global default). Artifacts with no
    resolvable retention period are left untouched (they never expire).

    The ``retention_policy`` column is stored as TEXT and may carry either a JSON
    object with a ``days`` key or a bare integer/string of days. When the column
    is JSON we honor an explicit ``days``; otherwise we fall back to the
    per-server / global resolution.

    Args:
        db: A connected database instance.
        config: Optional artifacts config dict.

    Returns:
        The number of artifacts whose ``expires_at`` was set.
    """
    if db is None:
        return 0

    try:
        rows = db.fetch_all(
            "SELECT id, server_id, retention_policy, created_at "
            "FROM artifacts WHERE expires_at IS NULL AND retention_policy IS NOT NULL"
        )
    except Exception as e:
        logger.error(
            f"Failed to read artifacts for retention windows: {e}", exc_info=True
        )
        return 0

    if not rows:
        return 0

    per_day_ms = _seconds_per_day() * 1000
    updated = 0
    for row in rows:
        artifact_id = row.get("id")
        server_id = row.get("server_id")
        policy = row.get("retention_policy")
        created_at = row.get("created_at")
        if artifact_id is None or created_at is None:
            continue

        policy_days = _parse_policy_days(policy)
        if policy_days is None:
            policy_days = resolve_retention_days(server_id, config)
        if policy_days is None or policy_days <= 0:
            continue

        expires_at = int(created_at) + policy_days * per_day_ms
        try:
            db.execute(
                "UPDATE artifacts SET expires_at = ? WHERE id = ?",
                (expires_at, artifact_id),
            )
            updated += 1
        except Exception as e:
            logger.warning(
                f"Failed to apply retention window for artifact {artifact_id}: {e}"
            )

    if updated:
        logger.info(f"Applied retention windows to {updated} artifact(s)")
    return updated


class RetentionCleanupJob:
    """Scheduled background worker that applies retention and purges expired rows.

    Mirrors the ``AccountReaper`` pattern: a daemon thread loop that sleeps for
    the configured interval and performs a cleanup cycle. The cycle first applies
    retention windows to artifacts that have a policy but no ``expires_at``, then
    purges rows whose window has elapsed (unless ``purge_expired`` is disabled).
    """

    def __init__(self, db: Any, config: Optional[Dict[str, Any]] = None) -> None:
        self._db = db
        self._config = config
        self._is_running = False
        self._thread: Optional[threading.Thread] = None

    def _interval_seconds(self) -> int:
        artifacts_cfg = _get_artifacts_config(self._config)
        retention_cfg = artifacts_cfg.get("retention", {}) or {}
        minutes = retention_cfg.get("run_cleanup_interval_minutes", 60)
        try:
            minutes = int(minutes)
        except (TypeError, ValueError):
            minutes = 60
        if minutes <= 0:
            minutes = 60
        return minutes * 60

    def _purge_enabled(self) -> bool:
        artifacts_cfg = _get_artifacts_config(self._config)
        retention_cfg = artifacts_cfg.get("retention", {}) or {}
        return bool(retention_cfg.get("purge_expired", True))

    def start(self) -> None:
        """Start the background cleanup loop."""
        if self._is_running:
            return
        if self._db is None:
            logger.info("Artifact retention cleanup skipped: no database")
            return

        self._is_running = True
        self._thread = threading.Thread(
            target=self._run_forever, daemon=True, name="RetentionCleanupJob"
        )
        self._thread.start()
        logger.info("Artifact retention cleanup job started")

    def stop(self) -> None:
        """Stop the background cleanup loop."""
        self._is_running = False
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
        self._thread = None

    def run_once(self, db: Any = None) -> Dict[str, int]:
        """Run a single cleanup cycle: apply windows then purge expired.

        Args:
            db: Optional db override; defaults to the db passed at construction.

        Returns:
            A dict with ``applied`` (windows applied) and ``purged`` (rows removed).
        """
        target_db = db if db is not None else self._db
        applied = _apply_retention_windows(target_db, self._config)
        purged = 0
        if self._purge_enabled():
            purged = purge_expired(target_db, self._config)
        else:
            logger.info(
                "Retention purge disabled (purge_expired=False): "
                "skipping deletion, only applying windows"
            )
        return {"applied": applied, "purged": purged}

    def _run_forever(self) -> None:
        interval = self._interval_seconds()
        while self._is_running:
            try:
                self.run_once()
            except Exception as e:
                logger.error(f"Retention cleanup cycle failed: {e}", exc_info=True)

            # Sleep in increments to allow faster shutdown.
            step = 10
            slept = 0
            while slept < interval and self._is_running:
                time.sleep(step)
                slept += step
