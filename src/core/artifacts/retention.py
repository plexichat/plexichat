"""
Artifact retention - purge of expired artifact rows.

Implements the minimal, real retention behavior referenced by the admin REST
endpoint: delete every artifact whose ``expires_at`` is set and already in the
past. Media/linked-row cascade cleanup is intentionally out of scope here
(matching the manager's `delete` semantics) and is handled by later groups.
"""

from typing import Any, Dict, Optional

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
    import time

    return int(time.time() * 1000)
