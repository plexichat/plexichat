"""Auth security hardening.

Adds a per-challenge attempt counter to 2FA challenges and a
case-insensitive uniqueness index on usernames to prevent lookalike
impersonation.  Idempotent so both clean and upgraded databases converge.
"""

import logging

logger = logging.getLogger(__name__)


def _execute(db, statement: str) -> None:
    converted = (
        db.convert_schema(statement) if hasattr(db, "convert_schema") else statement
    )
    db.execute(converted)


def up(db):
    # Per-challenge 2FA attempt counter (guarded: column may already exist on
    # deployments that ran a newer schema first).
    try:
        _execute(
            db,
            "ALTER TABLE auth_2fa_challenges ADD COLUMN attempts INTEGER DEFAULT 0",
        )
    except Exception as e:  # pragma: no cover - column already present
        logger.info(f"auth_2fa_challenges.attempts already present: {e}")

    # Make passkey columns part of the canonical auth_users schema so passkey
    # endpoints work even before any registration options are generated.
    for column_sql in (
        "ALTER TABLE auth_users ADD COLUMN passkey_enabled INTEGER DEFAULT 0",
        "ALTER TABLE auth_users ADD COLUMN webauthn_user_handle TEXT",
    ):
        try:
            _execute(db, column_sql)
        except Exception as e:  # pragma: no cover - column already present
            logger.info(f"auth_users column already present: {e}")

    # Case-insensitive username uniqueness (guarded: index can only be created
    # when existing data contains no case-insensitive duplicates).
    try:
        _execute(
            db,
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_auth_users_username_lower "
            "ON auth_users(LOWER(username))",
        )
    except Exception as e:
        logger.warning(
            "Could not create case-insensitive username index (pre-existing "
            f"duplicate usernames likely present): {e}"
        )


def down(db):
    try:
        _execute(db, "DROP INDEX IF EXISTS idx_auth_users_username_lower")
    except Exception:  # pragma: no cover
        pass
    try:
        _execute(db, "ALTER TABLE auth_2fa_challenges DROP COLUMN attempts")
    except Exception:  # pragma: no cover - column not present / unsupported
        pass
    for column_sql in (
        "ALTER TABLE auth_users DROP COLUMN passkey_enabled",
        "ALTER TABLE auth_users DROP COLUMN webauthn_user_handle",
    ):
        try:
            _execute(db, column_sql)
        except Exception:  # pragma: no cover - column not present / unsupported
            pass
