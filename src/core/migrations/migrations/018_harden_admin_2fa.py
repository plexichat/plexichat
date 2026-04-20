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
"""

import json
import logging

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

        # 3. Null out plaintext secrets ONLY for admins with verified encryption
        # This prevents data loss if the encryption key is misaligned.
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
                "Migration 018: Plaintext secrets cleared for %d verified admin(s)",
                len(verified_admin_ids),
            )
        else:
            logger.info(
                "Migration 018: No admins verified - plaintext secrets preserved as fallback"
            )

    except ImportError:
        logger.info(
            "Migration 018: Encryption module not available - skipping migration"
        )


def down(db):
    """Rollback the migration."""
    # We cannot reverse encryption, so we just note it
    # The plaintext columns were already cleared for migrated accounts
    pass
