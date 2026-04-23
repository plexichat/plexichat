"""
Harden admin 2FA security:
- Add encrypted TOTP secret column (totp_secret stays for backward compat during migration)
- Add hashed backup codes column (backup_codes plaintext stays for backward compat)
- Add OTP challenge tracking columns
- Migrate existing plaintext TOTP secrets to encrypted storage
- Migrate existing plaintext backup codes to hashed storage

Safety: plaintext columns are only NULLed after verifying that the encrypted
value can be successfully round-tripped (decrypt matches original). If
verification fails for any row, that row's plaintext is preserved.

Rollback: The down() function restores plaintext TOTP secrets by decrypting
the encrypted column back into totp_secret. A backup table is created
before any destructive operations to allow manual recovery.
"""

import json
import logging
import time as _time

logger = logging.getLogger(__name__)


def up(db):
    """Apply the migration."""

    # 1. Add new secure columns to admin_users
    try:
        db.execute("ALTER TABLE admin_users ADD COLUMN totp_secret_encrypted TEXT")
    except Exception:
        pass  # Column may already exist

    try:
        db.execute("ALTER TABLE admin_users ADD COLUMN backup_codes_hash TEXT")
    except Exception:
        pass  # Column may already exist

    try:
        db.execute("ALTER TABLE admin_users ADD COLUMN otp_last_used_code TEXT")
    except Exception:
        pass

    try:
        db.execute("ALTER TABLE admin_users ADD COLUMN otp_last_used_at INTEGER")
    except Exception:
        pass

    # 2. Migrate existing plaintext TOTP secrets to encrypted storage
    try:
        from src.utils.encryption import decrypt_data, encrypt_data, hash_password

        rows = db.fetch_all(
            "SELECT id, totp_secret, backup_codes FROM admin_users WHERE totp_secret IS NOT NULL"
        )

        # Track which admins had successful round-trip verification
        verified_admin_ids = []

        for row in rows:
            admin_id = row["id"] if isinstance(row, dict) else row[0]
            totp_secret = row["totp_secret"] if isinstance(row, dict) else row[1]
            backup_codes = row["backup_codes"] if isinstance(row, dict) else row[2]
            totp_verified = False

            # Encrypt the TOTP secret and verify round-trip
            if totp_secret:
                try:
                    encrypted = encrypt_data(
                        totp_secret, context=f"admin_totp:{admin_id}"
                    )
                    # Verify the encrypted value can be decrypted back to the original
                    decrypted = decrypt_data(
                        encrypted, context=f"admin_totp:{admin_id}"
                    )
                    if decrypted == totp_secret:
                        db.execute(
                            "UPDATE admin_users SET totp_secret_encrypted = ? WHERE id = ?",
                            (encrypted, admin_id),
                        )
                        totp_verified = True
                        logger.info(
                            "Migration 018: TOTP secret encrypted and verified for admin %s",
                            admin_id,
                        )
                    else:
                        logger.warning(
                            "Migration 018: TOTP round-trip verification FAILED for admin %s "
                            "- plaintext will be preserved",
                            admin_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "Migration 018: TOTP encryption failed for admin %s: %s "
                        "- plaintext will be preserved",
                        admin_id,
                        exc,
                    )

            # Hash backup codes
            if backup_codes:
                try:
                    codes = [c.strip() for c in backup_codes.split(",") if c.strip()]
                    hashed_codes = []
                    for code in codes:
                        normalized = code.replace("-", "").lower()
                        hashed = hash_password(normalized)
                        hashed_codes.append(hashed)
                    db.execute(
                        "UPDATE admin_users SET backup_codes_hash = ? WHERE id = ?",
                        (json.dumps(hashed_codes), admin_id),
                    )
                    logger.info(
                        "Migration 018: Backup codes hashed for admin %s",
                        admin_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "Migration 018: Backup code hashing failed for admin %s: %s",
                        admin_id,
                        exc,
                    )

            # Only mark this admin as verified if TOTP round-trip succeeded
            # (or they had no TOTP secret to begin with).
            # Note: if TOTP verification fails but backup codes were hashed,
            # the hashed codes persist but plaintext is preserved as fallback.
            if totp_verified or not totp_secret:
                verified_admin_ids.append(admin_id)

        # 3. Create a backup of admin plaintext secrets before nulling them,
        #    so they can be recovered if needed.
        backup_table = None
        if verified_admin_ids:
            backup_ts = int(_time.time())
            backup_table = f"admin_2fa_backup_{backup_ts}"
            try:
                db.execute(
                    f"""CREATE TABLE IF NOT EXISTS {backup_table} AS
                       SELECT id, totp_secret, backup_codes FROM admin_users
                       WHERE totp_secret IS NOT NULL OR backup_codes IS NOT NULL"""
                )
                logger.info(
                    "Migration 018: Plaintext secrets backed up to %s",
                    backup_table,
                )
            except Exception as backup_err:
                logger.warning(
                    "Migration 018: Could not create backup table %s: %s "
                    "- aborting plaintext nulling for safety",
                    backup_table,
                    backup_err,
                )
                # Do NOT null plaintext if we can't back it up
                verified_admin_ids = []

        # 4. Null out plaintext secrets ONLY for admins with verified encryption
        #    AND only after a backup table was successfully created.
        #    This prevents data loss if the encryption key is misaligned.
        if verified_admin_ids:
            placeholders = ",".join("?" for _ in verified_admin_ids)
            db.execute(
                f"UPDATE admin_users SET totp_secret = NULL "
                f"WHERE id IN ({placeholders}) AND totp_secret_encrypted IS NOT NULL",
                verified_admin_ids,
            )
            db.execute(
                f"UPDATE admin_users SET backup_codes = NULL "
                f"WHERE id IN ({placeholders}) AND backup_codes_hash IS NOT NULL",
                verified_admin_ids,
            )
            logger.info(
                "Migration 018: Plaintext secrets cleared for %d verified admin(s) "
                "(backup in %s)",
                len(verified_admin_ids),
                backup_table or "N/A",
            )
        else:
            logger.info(
                "Migration 018: No admins verified or backup failed - "
                "plaintext secrets preserved as fallback"
            )

    except ImportError:
        logger.info(
            "Migration 018: Encryption module not available - skipping migration"
        )


def down(db):
    """Rollback the migration.

    Restores plaintext TOTP secrets by decrypting the encrypted column
    back into totp_secret. Also clears the encrypted/hashed columns.
    If decryption fails for any admin, that admin's encrypted data is
    preserved but plaintext remains NULL — check the backup tables.
    """
    try:
        from src.utils.encryption import decrypt_data

        rows = db.fetch_all(
            "SELECT id, totp_secret_encrypted FROM admin_users "
            "WHERE totp_secret_encrypted IS NOT NULL AND totp_secret IS NULL"
        )

        restored_count = 0
        for row in rows:
            admin_id = row["id"] if isinstance(row, dict) else row[0]
            encrypted_secret = (
                row["totp_secret_encrypted"] if isinstance(row, dict) else row[1]
            )
            try:
                decrypted = decrypt_data(
                    encrypted_secret, context=f"admin_totp:{admin_id}"
                )
                db.execute(
                    "UPDATE admin_users SET totp_secret = ?, totp_secret_encrypted = NULL "
                    "WHERE id = ?",
                    (decrypted, admin_id),
                )
                restored_count += 1
            except Exception as exc:
                logger.warning(
                    "Migration 018 rollback: Could not decrypt TOTP secret for admin %s: %s "
                    "- check backup tables for manual recovery",
                    admin_id,
                    exc,
                )

        # Reset OTP columns to safe defaults for rollback
        # For admins whose plaintext was NOT restored, set them to a consistent
        # state: OTP disabled, must re-setup. This prevents the inconsistent
        # state of totp_enabled=1 with totp_secret=NULL.
        db.execute(
            "UPDATE admin_users SET totp_enabled = 0, must_setup_otp = 1, "
            "backup_codes_hash = NULL, otp_last_used_code = NULL, otp_last_used_at = NULL "
            "WHERE totp_secret IS NULL AND totp_secret_encrypted IS NOT NULL"
        )

        # For admins whose plaintext WAS restored, keep their OTP state intact.
        # Preserve backup_codes_hash — even though backup_codes is NULL,
        # the hashed codes are still the only functional backup mechanism
        # for these admins (hashes cannot be reversed to plaintext).
        db.execute(
            "UPDATE admin_users SET "
            "otp_last_used_code = NULL, otp_last_used_at = NULL "
            "WHERE totp_secret IS NOT NULL AND totp_secret_encrypted IS NULL"
        )

        if restored_count > 0:
            logger.info(
                "Migration 018 rollback: Restored %d plaintext TOTP secret(s)",
                restored_count,
            )
        else:
            logger.info(
                "Migration 018 rollback: No TOTP secrets restored "
                "(none needed restoring, or decryption failed)"
            )

    except ImportError:
        logger.info(
            "Migration 018 rollback: Encryption module not available - "
            "cannot restore plaintext secrets. Check backup tables."
        )
