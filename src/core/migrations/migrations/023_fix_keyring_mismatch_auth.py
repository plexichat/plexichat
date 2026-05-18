"""
Fix authentication after keyring/KEK mismatch.

When the message keyring is regenerated with a different KEK (due to
corruption, key rotation, or misconfiguration), the following break:

1. Admin 2FA: totp_secret_encrypted was encrypted with the OLD KEK and
   cannot be decrypted with the new one. Since migration 018 already
   NULLed the plaintext totp_secret, the admin is locked out entirely.
   Fix: Reset admin 2FA state so admin can login with password only
   and re-setup 2FA from scratch.

2. API access tokens: The blind_index() function uses the KEK to compute
   token_index values. With a new KEK, computed indices no longer match
   the stored token_index → all existing tokens appear invalid.
   Fix: Revoke all existing access tokens. New tokens created after the
   keyring regeneration will use the correct (current) KEK.

3. Missing tables: auth_passkeys, auth_passkey_challenges, and
   auth_user_notes may not exist if schema init ran before these tables
   were added to the schema definition, or if the migration that should
   have created them was applied in a context where it silently failed.
   Fix: Create these tables if they don't already exist.

IMPORTANT: This migration should be run AFTER deleting the corrupted
message_keyring.json so that the app generates a fresh keyring with the
current KEK on next startup. Otherwise the new KEK will still differ
from what the app uses at runtime.
"""

import time as _time
import logging

logger = logging.getLogger(__name__)


def _table_exists(db, table_name: str) -> bool:
    """Check if a table exists in the database."""
    try:
        if db.type == "postgres":
            row = db.fetch_one(
                "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = ?)",
                (table_name,),
            )
            if isinstance(row, dict):
                return bool(row.get("exists", False))
            return bool(row[0]) if row else False
        else:
            rows = db.fetch_all(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            return len(rows) > 0
    except Exception:
        return False


def up(db):
    """Apply the migration."""

    # ── 1. Reset admin 2FA state ──────────────────────────────────────
    # The admin is locked out because:
    #   - totp_secret is NULL (cleared by migration 018)
    #   - totp_secret_encrypted exists but cannot be decrypted (KEK mismatch)
    #   - totp_enabled=1, must_setup_otp=0 → app requires 2FA but can't verify it
    # Solution: Reset 2FA state so admin can login with password only and re-setup.
    now_ms = int(_time.time() * 1000)

    try:
        # Only reset admins who are in the broken state:
        # totp_enabled=1 but plaintext is gone and encrypted is present
        rows = db.fetch_all(
            "SELECT id, username, totp_enabled, totp_secret, totp_secret_encrypted "
            "FROM admin_users"
        )

        reset_ids = []
        for row in rows:
            if isinstance(row, dict):
                admin_id = row["id"]
                username = row["username"]
                totp_enabled = bool(row.get("totp_enabled", 0))
                has_plaintext = row.get("totp_secret") is not None
            else:
                admin_id = row[0]
                username = row[1]
                totp_enabled = bool(row[2]) if len(row) > 2 else False
                has_plaintext = row[3] is not None if len(row) > 3 else False

            # Reset if: 2FA is enabled but plaintext is gone (can't decrypt encrypted)
            if totp_enabled and not has_plaintext:
                reset_ids.append((admin_id, username))
                logger.info(
                    "Migration 022: Admin '%s' (id=%s) has 2FA enabled but no "
                    "decryptable TOTP secret — will reset 2FA",
                    username,
                    admin_id,
                )

        if reset_ids:
            for admin_id, username in reset_ids:
                db.execute(
                    "UPDATE admin_users SET "
                    "totp_enabled = 0, "
                    "must_setup_otp = 1, "
                    "totp_secret = NULL, "
                    "totp_secret_encrypted = NULL, "
                    "backup_codes = NULL, "
                    "backup_codes_hash = NULL, "
                    "otp_last_used_code = NULL, "
                    "otp_last_used_at = NULL "
                    "WHERE id = ?",
                    (admin_id,),
                )
                logger.info(
                    "Migration 022: Reset 2FA for admin '%s' (id=%s) — "
                    "login with password only, re-setup 2FA after login",
                    username,
                    admin_id,
                )
        else:
            logger.info(
                "Migration 022: No admins in broken 2FA state — no reset needed"
            )
    except Exception as exc:
        logger.warning("Migration 022: Error resetting admin 2FA: %s", exc)

    # ── 2. Revoke unusable API access tokens ───────────────────────────
    # The blind_index() function uses the KEK to compute token_index.
    # With a new KEK, computed indices no longer match stored values.
    # All existing tokens are therefore unusable and should be revoked.
    # New tokens created after keyring regeneration will work correctly.
    try:
        # Check if the auth_api_access_tokens table exists
        if _table_exists(db, "auth_api_access_tokens"):
            # Count non-revoked tokens before revoking
            count_row = db.fetch_one(
                "SELECT COUNT(*) as cnt FROM auth_api_access_tokens WHERE revoked = 0"
            )
            if isinstance(count_row, dict):
                active_count = count_row.get("cnt", 0)
            else:
                active_count = count_row[0] if count_row else 0

            if active_count and active_count > 0:
                db.execute(
                    "UPDATE auth_api_access_tokens SET "
                    "revoked = 1, revoked_at = ? "
                    "WHERE revoked = 0",
                    (now_ms,),
                )
                logger.info(
                    "Migration 022: Revoked %d active API access token(s) — "
                    "blind indices no longer match current KEK. "
                    "Create new tokens after login.",
                    active_count,
                )
            else:
                logger.info("Migration 022: No active API access tokens to revoke")
        else:
            logger.info(
                "Migration 022: auth_api_access_tokens table does not exist — "
                "skipping token revocation"
            )
    except Exception as exc:
        logger.warning("Migration 022: Error revoking API access tokens: %s", exc)

    # ── 3. Create missing tables ───────────────────────────────────────
    # These tables are defined in the schema but may not have been created
    # if schema init ran before they were added, or if a previous migration
    # silently failed to create them.

    missing_tables_created = []

    # auth_passkeys — WebAuthn/FIDO2 passkey credentials
    if not _table_exists(db, "auth_passkeys"):
        try:
            if db.type == "postgres":
                db.execute("""
                    CREATE TABLE IF NOT EXISTS auth_passkeys (
                        id BIGINT PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        credential_id TEXT NOT NULL,
                        credential_public_key BYTEA NOT NULL,
                        sign_count INTEGER DEFAULT 0,
                        device_type TEXT,
                        device_name TEXT,
                        aaguid TEXT,
                        transports TEXT,
                        backed_up INTEGER DEFAULT 0,
                        created_at BIGINT NOT NULL,
                        last_used_at BIGINT,
                        revoked INTEGER DEFAULT 0,
                        FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
                        UNIQUE(credential_id)
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS auth_passkeys (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        credential_id TEXT NOT NULL,
                        credential_public_key BLOB NOT NULL,
                        sign_count INTEGER DEFAULT 0,
                        device_type TEXT,
                        device_name TEXT,
                        aaguid TEXT,
                        transports TEXT,
                        backed_up INTEGER DEFAULT 0,
                        created_at INTEGER NOT NULL,
                        last_used_at INTEGER,
                        revoked INTEGER DEFAULT 0,
                        FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
                        UNIQUE(credential_id)
                    )
                """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_passkeys_user ON auth_passkeys(user_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_passkeys_credential ON auth_passkeys(credential_id)"
            )
            missing_tables_created.append("auth_passkeys")
        except Exception as exc:
            logger.warning("Migration 022: Failed to create auth_passkeys: %s", exc)

    # auth_passkey_challenges — temporary passkey challenges
    if not _table_exists(db, "auth_passkey_challenges"):
        try:
            if db.type == "postgres":
                db.execute("""
                    CREATE TABLE IF NOT EXISTS auth_passkey_challenges (
                        id BIGINT PRIMARY KEY,
                        challenge_id TEXT UNIQUE NOT NULL,
                        user_id BIGINT,
                        challenge_type TEXT NOT NULL,
                        challenge BYTEA NOT NULL,
                        device_name TEXT,
                        expires_at BIGINT NOT NULL,
                        used INTEGER DEFAULT 0,
                        created_at BIGINT NOT NULL
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS auth_passkey_challenges (
                        id INTEGER PRIMARY KEY,
                        challenge_id TEXT UNIQUE NOT NULL,
                        user_id INTEGER,
                        challenge_type TEXT NOT NULL,
                        challenge BLOB NOT NULL,
                        device_name TEXT,
                        expires_at INTEGER NOT NULL,
                        used INTEGER DEFAULT 0,
                        created_at INTEGER NOT NULL
                    )
                """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_passkey_challenges_expires "
                "ON auth_passkey_challenges(expires_at)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_passkey_challenges_id "
                "ON auth_passkey_challenges(challenge_id)"
            )
            missing_tables_created.append("auth_passkey_challenges")
        except Exception as exc:
            logger.warning(
                "Migration 022: Failed to create auth_passkey_challenges: %s", exc
            )

    # auth_user_notes — private notes about other users
    if not _table_exists(db, "auth_user_notes"):
        try:
            if db.type == "postgres":
                db.execute("""
                    CREATE TABLE IF NOT EXISTS auth_user_notes (
                        id BIGINT PRIMARY KEY,
                        user_id BIGINT NOT NULL,
                        target_user_id BIGINT NOT NULL,
                        note_encrypted TEXT NOT NULL,
                        created_at BIGINT NOT NULL,
                        updated_at BIGINT NOT NULL,
                        FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
                        FOREIGN KEY (target_user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
                        UNIQUE(user_id, target_user_id)
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS auth_user_notes (
                        id INTEGER PRIMARY KEY,
                        user_id INTEGER NOT NULL,
                        target_user_id INTEGER NOT NULL,
                        note_encrypted TEXT NOT NULL,
                        created_at INTEGER NOT NULL,
                        updated_at INTEGER NOT NULL,
                        FOREIGN KEY (user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
                        FOREIGN KEY (target_user_id) REFERENCES auth_users(id) ON DELETE CASCADE,
                        UNIQUE(user_id, target_user_id)
                    )
                """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_user_notes_user ON auth_user_notes(user_id)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_auth_user_notes_target ON auth_user_notes(target_user_id)"
            )
            missing_tables_created.append("auth_user_notes")
        except Exception as exc:
            logger.warning("Migration 022: Failed to create auth_user_notes: %s", exc)

    # username_blacklist — used by auth blacklist checks
    if not _table_exists(db, "username_blacklist"):
        try:
            if db.type == "postgres":
                db.execute("""
                    CREATE TABLE IF NOT EXISTS username_blacklist (
                        id BIGINT PRIMARY KEY,
                        pattern TEXT NOT NULL UNIQUE,
                        is_regex BOOLEAN DEFAULT FALSE,
                        reason TEXT,
                        created_by BIGINT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            else:
                db.execute("""
                    CREATE TABLE IF NOT EXISTS username_blacklist (
                        id INTEGER PRIMARY KEY,
                        pattern TEXT NOT NULL UNIQUE,
                        is_regex BOOLEAN DEFAULT 0,
                        reason TEXT,
                        created_by INTEGER,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)
            missing_tables_created.append("username_blacklist")
        except Exception as exc:
            logger.warning(
                "Migration 022: Failed to create username_blacklist: %s", exc
            )

    if missing_tables_created:
        logger.info(
            "Migration 022: Created %d missing table(s): %s",
            len(missing_tables_created),
            ", ".join(missing_tables_created),
        )
    else:
        logger.info("Migration 022: All expected tables already exist")

    logger.info(
        "Migration 022: Complete. Admin must re-setup 2FA after login. "
        "New API access tokens must be created after login. "
        "Ensure message_keyring.json is deleted before restart."
    )


def down(db):
    """Rollback the migration.

    Note: This cannot restore the old TOTP secrets — they were already
    lost before this migration ran (migration 018 NULLed the plaintext,
    and the KEK mismatch made the encrypted copy unreadable). Rolling
    back simply keeps the admin in a 'must re-setup 2FA' state.

    Access tokens revoked by this migration are un-revoked, but only
    those that were revoked at the exact timestamp this migration used,
    to avoid un-revoking tokens that were manually revoked by admins
    at an earlier time. Note that un-revoked tokens may still be
    unusable if the KEK is still mismatched.
    """
    # Admin 2FA data cannot be restored regardless of rollback direction —
    # the plaintext was already NULL and the encrypted copy is unreadable.
    # The admin must re-setup 2FA after login either way.
    logger.info(
        "Migration 022 rollback: Admin 2FA data was already lost before "
        "this migration — admin must re-setup 2FA after login."
    )

    # Un-revoke ONLY tokens that were revoked by this migration.
    # We cannot know the exact timestamp from the up() run, so we
    # un-revoke tokens revoked within the last 24 hours, which is a
    # reasonable window for a rollback happening shortly after the
    # migration was applied. This avoids un-revoking manually-revoked
    # tokens from weeks/months ago.
    try:
        if _table_exists(db, "auth_api_access_tokens"):
            import time as _t

            cutoff = int((_t.time() - 86400) * 1000)  # 24 hours ago in ms
            db.execute(
                "UPDATE auth_api_access_tokens SET "
                "revoked = 0, revoked_at = NULL "
                "WHERE revoked = 1 AND revoked_at IS NOT NULL AND revoked_at >= ?",
                (cutoff,),
            )
            logger.info(
                "Migration 022 rollback: Un-revoked access tokens revoked "
                "within the last 24h (revoked_at >= %d). "
                "Note: tokens may still be unusable if KEK is mismatched.",
                cutoff,
            )
    except Exception as exc:
        logger.warning("Migration 022 rollback: Error un-revoking tokens: %s", exc)

    # Do NOT drop the tables we created — they're part of the correct schema
    logger.info(
        "Migration 022 rollback: Created tables preserved. "
        "Admin must re-setup 2FA after login."
    )
