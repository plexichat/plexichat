"""
Private helper functions for admin authentication.
"""

import hashlib
import os
import secrets
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import utils.logger as logger
from src.core.auth.replay import BoundedReplayGuard


# An OTP challenge is a single-attempt credential: validation must consume it.
# The payload itself only lives in the cache tier, where GET + DELETE is a
# check-then-delete TOCTOU — two concurrent verifications both read a live
# challenge before either removes it, and because the per-code CAS in
# ``OTPMixin.verify_otp`` only rejects reuse of the *same* code, two different
# still-valid TOTP codes (valid_window=1 admits three) both mint a session from
# one challenge. A separate atomic claim closes that gap: Valkey SET NX spans
# workers, and the bounded local guard covers single-worker/offline mode.
_OTP_CHALLENGE_CLAIM_PREFIX = "admin_otp_claim:"
# Matches ``_create_otp_challenge``'s default (and only) 5-minute lifetime. The
# per-claim TTL is further clamped to the challenge's remaining lifetime below.
_OTP_CHALLENGE_CLAIM_TTL_SECONDS = 300
_OTP_CHALLENGE_CLAIM_GUARD = BoundedReplayGuard(
    max_entries=4096, ttl_seconds=_OTP_CHALLENGE_CLAIM_TTL_SECONDS
)


def _claim_otp_challenge_once(fingerprint: str, ttl_seconds: int) -> bool:
    """Atomically claim one challenge; True only for the first caller.

    Returns False on a replay (another worker/consumer already claimed it).
    The claim is a bounded, self-expiring marker, so it cannot grow without
    limit and never outlives the challenge it guards.
    """
    key = f"{_OTP_CHALLENGE_CLAIM_PREFIX}{fingerprint}"
    try:
        from src.core.database.valkey_client import (
            ValkeyOperationError,
            get_client,
            is_available,
        )

        client = get_client()
        if client is not None and is_available():
            set_nx = getattr(client, "set_nx", None)
            if callable(set_nx):
                try:
                    return bool(set_nx(key, "1", ttl=ttl_seconds))
                except ValkeyOperationError:
                    # Shared backend failed - fall through to the bounded
                    # process-local guard rather than accepting the replay.
                    pass
    except Exception:
        pass
    return _OTP_CHALLENGE_CLAIM_GUARD.consume(key)


def _hash_admin_token(token: str) -> str:
    """Return a deterministic one-way fingerprint for an admin bearer token.

    Password hashes must be salted, but session validation needs to look up a
    row from the presented token. The token is generated from high-entropy
    randomness, so a SHA-256 fingerprint is appropriate here and avoids
    storing the bearer value itself. This is deliberately separate from the
    Argon2id password hashing path.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _otp_challenge_fingerprint(challenge_token: str) -> str:
    """Deterministic one-way fingerprint for a live OTP challenge token.

    The challenge token is returned to the admin client after the password
    phase and, until it expires, is the only thing binding an OTP code to that
    login attempt. Writing it verbatim into a cache key copies the credential
    into Valkey's keyspace (``KEYS admin_otp_challenge:*``) and into any
    process-local cache dump, where operators and log shippers with no need to
    know the secret can read it. Hash it first, mirroring ``_hash_admin_token``;
    callers still present the raw token and every lookup re-derives the same
    fingerprint, so the one-way store is transparent to the flow. Fail closed
    on malformed input rather than ever echoing it.
    """
    if not isinstance(challenge_token, str) or not challenge_token:
        return "invalid"
    return hashlib.sha256(challenge_token.encode("utf-8")).hexdigest()


def _create_otp_challenge(admin_id: int, is_setup: bool, ttl_seconds: int = 300) -> str:
    """Create a short-lived challenge token for OTP verification binding."""
    from src.core.database import cache_set

    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + ttl_seconds
    payload = {
        "admin_id": admin_id,
        "is_setup": is_setup,
        "expires_at": expires_at,
    }
    # The raw token never enters the cache keyspace.
    cache_set(
        f"admin_otp_challenge:{_otp_challenge_fingerprint(token)}",
        payload,
        ttl=ttl_seconds,
    )
    return token


def _validate_otp_challenge(
    challenge_token: str, admin_id: int, is_setup: bool
) -> bool:
    """Validate OTP challenge token against admin and flow type."""
    from src.core.database import cache_get

    now = int(time.time())
    key = f"admin_otp_challenge:{_otp_challenge_fingerprint(challenge_token)}"
    payload = cache_get(key)

    if not payload:
        # Never log the key: it used to embed the raw challenge token, which
        # copied a live second-factor credential into shipped debug logs.
        logger.debug("OTP validation failed: unknown challenge")
        return False

    if payload["expires_at"] < now:
        logger.debug("OTP validation failed: Challenge expired")
        return False

    if payload["admin_id"] != admin_id or payload["is_setup"] != is_setup:
        logger.debug(
            f"OTP validation failed: Payload mismatch (Expected ID {admin_id}, got {payload['admin_id']}; Setup Expected {is_setup}, got {payload['is_setup']})"
        )
        return False

    # SECURITY: single-use. Everything above is a cache read (a fast-fail); the
    # authoritative single-winner decision is this atomic claim. Without it two
    # concurrent verifications both pass the read before either calls
    # ``_consume_otp_challenge`` (a plain DELETE), so the challenge is replayable.
    # Clamp the claim TTL to the challenge's remaining lifetime so the marker
    # never outlives the credential it guards and is always bounded.
    try:
        remaining = max(
            1, min(_OTP_CHALLENGE_CLAIM_TTL_SECONDS, int(payload["expires_at"]) - now)
        )
    except (TypeError, ValueError, KeyError):
        remaining = _OTP_CHALLENGE_CLAIM_TTL_SECONDS
    if not _claim_otp_challenge_once(
        _otp_challenge_fingerprint(challenge_token), remaining
    ):
        logger.debug("OTP validation failed: challenge already consumed")
        return False

    return True


def _check_otp_attempt_limit(
    challenge_token: str, max_attempts: int = 5, window_seconds: int = 300
) -> bool:
    """Atomically enforce a bounded OTP challenge attempt budget."""
    from src.core.database import check_rate_limit

    allowed, _remaining = check_rate_limit(
        f"admin_otp:{challenge_token}", max_attempts, window_seconds
    )
    return bool(allowed)


def _consume_otp_challenge(challenge_token: str) -> None:
    from src.core.database import cache_delete

    cache_delete(f"admin_otp_challenge:{_otp_challenge_fingerprint(challenge_token)}")
    # The atomic rate-limit bucket expires naturally; explicitly deleting the
    # challenge is sufficient and avoids a second non-atomic counter path.


def _generate_password(length: int = 24) -> str:
    """Generate a CSPRNG password that always satisfies the shared policy.

    The old implementation drew each character independently from
    ``string.ascii_letters + string.digits + "!@#$%^&*"``. With 24 draws it
    omitted a special character ~5.4% of the time and a digit ~2.5% of the
    time, so ~8% of first-boot / reset admin credentials failed the very
    character-class policy every other credential-setting path enforces.
    Delegate to the policy-compliant generator: every required class is
    guaranteed deterministically while the full CSPRNG strength is kept.
    """
    from src.core.auth.passwords import generate_policy_compliant_password

    # token_urlsafe(n) yields ~4n/3 base64url characters (~6 bits each). Ask
    # for at least 32 bytes (>=256 bits) and never fewer bytes than the
    # requested length needs, so the entropy is preserved (in fact raised well
    # above the old ~147-bit draw) for every caller.
    token_bytes = max(32, (int(length) * 3 + 3) // 4)
    return generate_policy_compliant_password(token_bytes=token_bytes)


def _verify_admin_password(
    db: Any, admin_id: int, current_password: str
) -> Tuple[bool, Optional[Dict[str, Any]], str]:
    """Verify the current admin password and return the account row."""
    # Fetch every admin_users column the self-service credential flows need
    # in a single round trip. Callers used to re-read totp_secret /
    # totp_secret_encrypted / backup_codes_hash / totp_enabled in a second
    # query after this one; those columns are always available on the row we
    # already pay for (the expensive part is the Argon2 verify below, not the
    # read). Adding columns is backward compatible: every caller accesses the
    # row by key.
    row = db.fetch_one(
        """
        SELECT username, email, password_hash, created_at, last_login,
               totp_enabled, must_setup_otp, backup_codes,
               totp_secret, totp_secret_encrypted, backup_codes_hash
        FROM admin_users
        WHERE id = ?
        """,
        (admin_id,),
    )
    if not row:
        return False, None, "Admin user not found"

    if not isinstance(row, dict):
        row = {
            "username": row[0],
            "email": row[1],
            "password_hash": row[2],
            "created_at": row[3],
            "last_login": row[4],
            "totp_enabled": row[5],
            "must_setup_otp": row[6],
            "backup_codes": row[7],
            "totp_secret": row[8] if len(row) > 8 else None,
            "totp_secret_encrypted": row[9] if len(row) > 9 else None,
            "backup_codes_hash": row[10] if len(row) > 10 else None,
        }

    import src.utils.encryption as encryption

    if not encryption.verify_password(current_password, row["password_hash"]):
        return False, None, "Incorrect current password"
    return True, row, ""


def _save_admin_credentials(password: str) -> None:
    """Hand the generated bootstrap admin password to the operator.

    The plaintext password is written to a credential file at rest
    (~/.plexichat/admin_credentials.txt, mode 0600, atomic write) so it
    survives container recreation and operator reboots. The write path is
    hardened: mkstemp with 0600 + atomic replace, and any failure is FATAL
    (raises) so first boot can never silently fall back to an unknown
    password.

    The password is also printed ONCE to the operator console (stdout) at
    the moment of generation so interactive operators can capture it
    directly.

    Set PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS=1 to disable the disk write
    (console output is always emitted).
    """
    opt_out = os.environ.get("PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS", "") == "1"

    # Always surface the password exactly once on the console.
    print(
        "\n"
        + "=" * 60
        + "\nPLEXICHAT FIRST BOOT: generated admin credentials"
        + "\n  Username: admin"
        + f"\n  Password: {password}"
        + "\n  Saved to ~/.plexichat/admin_credentials.txt (mode 0600)"
        + "\n  Set PLEXICHAT_NO_SAVE_ADMIN_CREDENTIALS=1 to disable disk write."
        + "\n"
        + "=" * 60
        + "\n",
        flush=True,
    )

    if opt_out:
        return

    home_dir = Path.home() / ".plexichat"
    creds_file = home_dir / "admin_credentials.txt"
    home_dir.mkdir(parents=True, exist_ok=True)
    content = f"""Plexichat Admin Credentials
============================
Generated: {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")}

Username: admin
Password: {password}
Email: admin@example.com (change this in admin settings)

IMPORTANT:
- Change this password after first login
- Set up 2FA (required on first login)
- Protect this file (mode 0600); anyone who reads it can sign in as admin

Login URL: https://<your-server>:8000/admin
"""
    temporary_path = None
    try:
        # Write beside the destination with restrictive creation mode, then
        # atomically replace the destination. This avoids exposing new
        # credentials through an existing permissive file mode and preserves
        # the old file if secure preparation fails.
        fd, temporary_path = tempfile.mkstemp(
            prefix=f".{creds_file.name}.", suffix=".tmp", dir=str(home_dir)
        )
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as output:
            output.write(content)
        os.replace(temporary_path, creds_file)
        temporary_path = None
    except OSError as exc:
        logger.critical(
            "Could not create private admin credential file %s: %s",
            creds_file,
            exc,
        )
        raise RuntimeError("Admin credentials could not be stored securely") from exc
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
    logger.warning(f"Admin credentials file written (operator opt-in): {creds_file}")


def _sync_password_to_auth_users(db: Any, username: str, password_hash: str) -> None:
    """Sync the password hash from admin_users to auth_users for the given username.

    Keeps both tables consistent so any code path that checks auth_users
    (e.g. user-facing APIs, future auth unification) sees the correct hash.
    Also ensures cache invalidation so the new hash is picked up immediately.
    """
    try:
        row = db.fetch_one("SELECT id FROM auth_users WHERE username = ?", (username,))
        if row:
            user_id = row["id"] if isinstance(row, dict) else row[0]
            db.execute(
                "UPDATE auth_users SET password_hash = ? WHERE id = ?",
                (password_hash, user_id),
            )
            # SECURITY: the hash written above is the account's main-app
            # credential. A credential reset must retire every session that was
            # authenticated with the old one, otherwise a stolen session token
            # survives the reset (the user-facing password-change paths already
            # do this). Without this, an admin password change only killed the
            # admin-panel sessions, leaving the account reachable via auth_users.
            db.execute(
                "UPDATE auth_sessions SET revoked = 1 WHERE user_id = ?",
                (user_id,),
            )
            try:
                from src.core.database import invalidate_pattern

                invalidate_pattern(f"user_data:{user_id}*")
                # Cached token verifications would otherwise replay a
                # pre-reset "valid" answer for up to the verify-cache TTL.
                invalidate_pattern("token_verify:*")
            except Exception as cache_error:
                # The admin action already committed; the cache TTL is the floor.
                logger.debug(f"admin user cache invalidate failed: {cache_error}")
        else:
            logger.warning(
                "No auth_users row for admin '%s' — password not synced", username
            )
    except Exception as exc:
        logger.error(
            "Failed to sync password to auth_users for '%s': %s", username, exc
        )


def _check_rate_limit(
    ip: str,
    max_attempts: int = 5,
    window_seconds: int = 300,
    lockout_seconds: int = 900,
) -> Tuple[bool, Optional[int]]:
    """Atomically limit admin login attempts by the trusted client address.

    The limiter consumes one slot for each login attempt. Successful login
    resets the bucket in ``AuthenticationMixin``; failed attempts remain
    counted across workers and processes.
    """
    from src.core.database import check_rate_limit

    # Use the longer lockout window for the shared atomic bucket. This avoids
    # the old cache_get/list/cache_set race and keeps the denial effective
    # across workers without a separate non-atomic lockout key.
    allowed, _remaining = check_rate_limit(
        f"admin_login:{ip}", max_attempts, max(window_seconds, lockout_seconds)
    )
    if allowed:
        return True, None
    return False, lockout_seconds


# The ``admin_login:{ip}`` bucket above bounds guesses from ONE source, but a
# caller with a botnet (or a trusted-proxy header chain it controls) gets a
# fresh bucket for every attempt and can guess the single admin password
# forever. Admin OTP already has an account-scoped budget (``admin_otp_account:``);
# the password phase needs the same invariant. Keyed by the admin id resolved
# from the admin_users row -- never the submitted username -- so a client cannot
# pick, rotate, or collide with another account's bucket. Raised limiter faults
# deny the attempt instead of reopening unlimited guessing.
ADMIN_PASSWORD_MAX_ATTEMPTS = 5
ADMIN_PASSWORD_WINDOW_SECONDS = 900


def _check_admin_password_budget(admin_id: int) -> bool:
    """Consume one account-scoped admin password guess; fail closed on faults."""
    from src.core.database import check_rate_limit

    try:
        allowed, _remaining = check_rate_limit(
            f"admin_login_account:{admin_id}",
            ADMIN_PASSWORD_MAX_ATTEMPTS,
            ADMIN_PASSWORD_WINDOW_SECONDS,
        )
    except Exception:
        logger.error("admin password limiter: store error - failing closed")
        return False
    return bool(allowed)
