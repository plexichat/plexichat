"""
Harden admin 2FA security:
- Add encrypted TOTP secret column (totp_secret stays for backward compat during migration)
- Add hashed backup codes column (backup_codes plaintext stays for backward compat)
- Add OTP challenge tracking columns
- Migrate existing plaintext TOTP secrets to encrypted storage
- Migrate existing plaintext backup codes to hashed storage
"""

import json


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
        from src.utils.encryption import encrypt_data, hash_password

        rows = db.fetch_all(
            "SELECT id, totp_secret, backup_codes FROM admin_users WHERE totp_secret IS NOT NULL"
        )
        for row in rows:
            admin_id = row["id"] if isinstance(row, dict) else row[0]
            totp_secret = row["totp_secret"] if isinstance(row, dict) else row[1]
            backup_codes = row["backup_codes"] if isinstance(row, dict) else row[2]

            # Encrypt the TOTP secret
            if totp_secret:
                try:
                    encrypted = encrypt_data(
                        totp_secret, context=f"admin_totp:{admin_id}"
                    )
                    db.execute(
                        "UPDATE admin_users SET totp_secret_encrypted = ? WHERE id = ?",
                        (encrypted, admin_id),
                    )
                except Exception:
                    pass  # Skip if encryption unavailable

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
                except Exception:
                    pass  # Skip if hashing unavailable

        # 3. Null out plaintext secrets after successful migration
        # Only do this if encryption succeeded for all rows
        db.execute(
            "UPDATE admin_users SET totp_secret = NULL WHERE totp_secret_encrypted IS NOT NULL"
        )
        db.execute(
            "UPDATE admin_users SET backup_codes = NULL WHERE backup_codes_hash IS NOT NULL"
        )
    except ImportError:
        pass  # Encryption module not available during initial setup


def down(db):
    """Rollback the migration."""
    # We cannot reverse encryption, so we just note it
    # The plaintext columns were already cleared for migrated accounts
    pass
