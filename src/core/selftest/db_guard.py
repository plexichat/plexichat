"""Self-test DB guard and error sanitization utilities.

The disposable-DB guard prevents the self-test suite from running
destructive operations (whole-table wipes, migration rollbacks) against
a live/production database. It must be consulted by every service that
performs side-effecting SQL before executing it.
"""

import os
from typing import Any, Tuple


DISPOSABLE_ENV_MARKER = "PLEXICHAT_SELFTEST_DISPOSABLE_DB"


def is_disposable_database(db: Any, config: dict) -> Tuple[bool, str]:
    """Return (allowed, reason) for whether the self-test suite may use db.

    The suite is allowed to run destructive operations only when:

    * The process is explicitly marked disposable via the
      ``PLEXICHAT_SELFTEST_DISPOSABLE_DB`` environment variable, or
    * The caller opts out via ``config['require_disposable_db'] = False``, or
    * The db instance carries a ``disposable`` attribute that is truthy, or
    * The db path matches the selftest scratch-path convention
      (``/data/selftest_*``).

    Every service-layer method that performs side-effecting SQL (whole-table
    wipes, migration apply/rollback, privilege grants) MUST call this
    before executing and refuse with a clear error when it returns False.
    """
    if config.get("require_disposable_db") is False:
        return True, "opt-out of disposable-DB guard"

    if getattr(db, "disposable", False):
        return True, "db marked disposable by attribute"

    if os.environ.get(DISPOSABLE_ENV_MARKER):
        return True, "env marker set"

    db_path = getattr(getattr(db, "config", {}), "get", lambda *_: "")("path", "")
    if db_path and db_path.startswith("/data/selftest_"):
        return True, "selftest scratch path"

    return False, "db is not a disposable selftest database"


def sanitize_error(value: Any, limit: int = 200) -> str:
    """Sanitize raw exception/error strings before storing shared state.

    Error details end up in shared result state that can be surfaced by
    status endpoints, so they are reduced to a single-line, truncated
    summary (exception type + message) with control characters removed.
    """
    if isinstance(value, BaseException):
        text = f"{type(value).__name__}: {value}"
    else:
        try:
            text = str(value)
        except Exception:
            return "<unprintable error>"
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[:limit] + "...[truncated]"
    return text
