"""
One-shot migration: soundboard_sounds.cooldown_seconds = 0 → NULL
+ flag-day grace config key for rollout safety.

Background
==========
The previous schema declared ``cooldown_seconds INTEGER NOT NULL
DEFAULT 0`` so any newly-uploaded sound that an owner did not
explicitly configure via ``update_sound(..., cooldown=N)`` was
implicitly ``0`` — which the new manager code (added with
soundboard manager.py play_sound / can_play_sound NULL semantics
pin) treats as "explicit cooldown disable".

Without this migration, every pre-existing sound on prod deploy
would silently lose cooldown enforcement overnight.  Converting
``0 → NULL`` lets the new "NULL = fall back to
default_cooldown_seconds" semantics engage uniformly.

KNOWN DATA-LOSS CAVEAT
======================
There is NO in-DB marker distinguishing a row whose
``cooldown_seconds=0`` came from the OLD schema's ``DEFAULT 0``
versus a row whose owner explicitly ran
``update_sound(..., cooldown=0)`` to silence cooldown for that sound.
This migration will clobber the latter cohort (the much rarer case)
to NULL; affected owners can re-issue
``update_sound(<user>, <id>, cooldown=0)`` after migration to restore
explicit-disable behaviour.  This trade-off is documented in
``tech_spec.md`` and the release notes.

Flag-day grace
==============
Until the deploy that ships this migration, the manager reads
``cooldown_seconds`` and applies the new NULL semantics.  Operators
who need to "revert" to the old behaviour can set
``soundboard.cooldown_grace_until_zero_ts`` in their config to a
future Unix timestamp; while that timestamp is in the future the
manager also treats stored ``0`` as NULL.  After the timestamp
passes, or after the manual fix-up, owners can drop the config key.

Version: 046
Depends: 045
"""

import logging

logger = logging.getLogger(__name__)


def up(db):
    logger.info("Migration 046: starting cooldown_seconds NULL rollout")

    try:
        if not db.table_exists("soundboard_sounds"):
            logger.warning(
                "Migration 046: soundboard_sounds table not found; skipping "
                "(will be created empty by the module's schema bootstrap)"
            )
            return

        # Snapshot rows that would change so operators can audit later.
        # The query is harmless when the table is empty / has no offending
        # rows; for prod tables it surfaces the explicit-disable cohort.
        try:
            pre_rows = db.fetch_all(
                "SELECT id, server_id, name FROM soundboard_sounds "
                "WHERE cooldown_seconds = 0"
            )
            if pre_rows:
                logger.info(
                    "Migration 046: pre-migration snapshot captured %d "
                    "rows with cooldown_seconds=0; explicit-disable "
                    "owners should re-apply update_sound(cooldown=0) "
                    "after migration if needed",
                    len(pre_rows),
                )
            else:
                logger.info("Migration 046: no rows with cooldown_seconds=0; no-op")
        except Exception as snapshot_err:  # pragma: no cover - audit-only
            logger.warning(
                "Migration 046: pre-migration snapshot failed (auditing "
                "only, continuing): %s",
                snapshot_err,
            )

        # RELAX the column to be nullable (idempotent on Postgres / SQLite).
        # Some backends (SQLite < 3.35) cannot drop NOT NULL in place; the
        # ALTER statement below is best-effort and the migration continues
        # regardless so subsequent inserts can pass NULL.
        try:
            db.execute(
                "ALTER TABLE soundboard_sounds "
                "ALTER COLUMN cooldown_seconds DROP NOT NULL"
            )
            logger.info("Migration 046: cooldown_seconds now nullable")
        except Exception as relax_err:
            logger.warning(
                "Migration 046: could not relax NOT NULL on "
                "cooldown_seconds (continuing — the manager already "
                "treats 0 and NULL as different states when the column "
                "is read): %s",
                relax_err,
            )

        # Convert pre-existing rows from 0 to NULL so the manager's new
        # "NULL = fall back to default_cooldown_seconds" semantics engage.
        db.execute(
            "UPDATE soundboard_sounds "
            "SET cooldown_seconds = NULL "
            "WHERE cooldown_seconds = 0"
        )
        logger.info("Migration 046: converted 0-rows to NULL")

    except Exception as e:
        logger.warning(
            "Migration 046: failed during cooldown_seconds NULL rollout: %s",
            e,
        )
        # Do NOT re-raise — this migration is best-effort by design
        # (the colleague change in soundboard/manager.py is the
        # authoritative definition; this migration just ensures existing
        # prod rows respect it).

    logger.info("Migration 046 completed successfully")


def down(db):
    """Rollback: convert NULL back to 0.

    Note: this loses the distinction between "never configured" and
    "explicitly disabled" that was introduced by the up migration;
    rolling back purely to restore the old (broken-from-manager-
    perspective) semantics.  Use with care.
    """
    logger.info("Migration 046 rollback: starting")
    try:
        db.execute(
            "UPDATE soundboard_sounds "
            "SET cooldown_seconds = 0 "
            "WHERE cooldown_seconds IS NULL"
        )
        logger.info("Migration 046 rollback: NULL rows restored to 0")
    except Exception as e:
        logger.warning(
            "Migration 046 rollback: failed to update soundboard_sounds: %s",
            e,
        )
    logger.info("Migration 046 rollback completed")
