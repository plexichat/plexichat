"""
TOTP (Time-based One-Time Password) implementation for 2FA.

Compatible with Google Authenticator, Authy, and other TOTP apps.
Includes replay attack prevention and DoS mitigation for backup codes.
"""

import os
import time
import hashlib
import hmac
import threading
import importlib
from typing import List, Tuple, Optional, Dict, Any, Union

try:
    qrcode = importlib.import_module("qrcode")
except Exception:
    qrcode = None
    import logging as _logging

    _logging.getLogger(__name__).info(
        "qrcode module not available — QR code generation will be disabled"
    )

import utils.config as config

# Import encryption for storing secrets
from src.utils.encryption import (
    encrypt_data,
    decrypt_data,
)

# pyotp for TOTP
import pyotp
import utils.logger as logger


class TOTPReplayCache:
    """
    Thread-safe cache to prevent TOTP code replay attacks.

    Tracks used codes within their validity window to prevent reuse.
    """

    def __init__(self, ttl_seconds: int = 90):
        """
        Initialize replay cache.

        Args:
            ttl_seconds: How long to remember used codes (should be >= TOTP interval + window)
        """
        self._used_codes: Dict[str, int] = {}  # key -> expiry_timestamp
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._last_cleanup = 0
        # Shared-database claim store (set via set_db). Used when no Valkey
        # backend is configured so replay protection still holds across
        # workers on the same database.
        self._db: Optional[Any] = None
        self._db_table_ready = False
        self._db_lock = threading.Lock()
        self._last_db_cleanup = 0

    def set_db(self, db: Any) -> None:
        """Attach the shared database used for cross-worker replay claims."""
        self._db = db
        self._db_table_ready = False

    def _ensure_db_table(self) -> bool:
        """Create the claim table on first use. Returns False when unusable."""
        db = self._db
        if db is None:
            return False
        if self._db_table_ready:
            return True
        with self._db_lock:
            if self._db_table_ready:
                return True
            try:
                from .schema import ensure_totp_replay_table

                ensure_totp_replay_table(db)
                self._db_table_ready = True
                return True
            except Exception as e:
                logger.warning(
                    f"TOTP replay cache: could not prepare the shared claim table: {e}"
                )
                return False

    def _db_cleanup(self, now_ms: int) -> None:
        """Best-effort purge of expired claims (rate-limited to once per 5min)."""
        if now_ms - self._last_db_cleanup < 300_000:
            return
        self._last_db_cleanup = now_ms
        db = self._db
        if db is None:
            return
        try:
            db.execute("DELETE FROM auth_totp_replay WHERE expires_at <= ?", (now_ms,))
        except Exception:
            pass

    def _db_cache_claim(self, key: str, ttl_seconds: int) -> Optional[bool]:
        """Claim ``key`` in the shared database.

        Returns:
            True  - claim succeeded (code not seen before in any worker).
            False - replay detected (a live claim already exists).
            None  - no usable database; caller must fall back to the
                    process-local cache.

        ``insert_or_ignore`` is an atomic primary-key claim on both SQLite
        and PostgreSQL, so exactly one worker wins even under concurrency.
        """
        db = self._db
        if db is None or not self._ensure_db_table():
            return None
        # Millisecond resolution: second-resolution expiry made the claim live
        # anywhere from 0 to ttl seconds, so a claim made just before a second
        # boundary was already expired for the replay that followed it. The
        # column is INTEGER and holds milliseconds (see auth/schema.py).
        now_ms = int(time.time() * 1000)
        expires = now_ms + int(ttl_seconds * 1000)
        try:
            # Free an expired leftover first so a stale row cannot block a
            # legitimate new window; the conditional delete is atomic and the
            # subsequent insert is the actual single-winner claim.
            db.execute(
                "DELETE FROM auth_totp_replay WHERE claim_key = ? AND expires_at <= ?",
                (key, now_ms),
            )
            claimed = db.insert_or_ignore(
                "auth_totp_replay", ["claim_key", "expires_at"], (key, expires)
            )
            self._db_cleanup(now_ms)
            return bool(claimed)
        except Exception as e:
            logger.warning(f"TOTP replay cache: shared database fallback failed: {e}")
            return None

    @staticmethod
    def _shared_cache_claim(key: str, ttl_seconds: int) -> Optional[bool]:
        """Try to claim ``key`` once in the shared (Valkey) cache.

        Returns:
            True  - claim succeeded (code not seen before, now marked used).
            False - replay detected (key already claimed).
            None  - no shared backend configured or the backend errored;
                    caller must fall back to the process-local cache.
        """
        try:
            from src.core.database.valkey_client import (
                get_client,
                is_available,
                ValkeyOperationError,
            )
        except Exception:  # pragma: no cover - defensive
            return None

        client = get_client()
        if not client or not is_available():
            return None
        full_key = f"totp_replay:{key}"
        try:
            # SET NX + EXPIRE is an atomic claim-and-mark: exactly one worker
            # wins, every other worker sees the replay. A plain SET would
            # overwrite the previous claim and accept the replay, so the
            # conditional variant is load-bearing here.
            set_nx = getattr(client, "set_nx", None)
            if not callable(set_nx):  # pragma: no cover - back-compat
                # Without a conditional SET there is no atomic claim; fall
                # through to the shared-database claim instead of
                # overwriting with a plain SET (which would accept replays).
                return None
            claimed = set_nx(full_key, "1", ttl_seconds)
            return bool(claimed)
        except ValkeyOperationError:
            logger.warning(
                "TOTP replay cache: shared backend error - falling back to "
                "process-local replay window"
            )
            return None
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"TOTP replay cache: unexpected shared-cache error: {e}")
            return None

    def _cleanup(self, now: int) -> None:
        """Remove expired entries. Called while holding lock."""
        # Cleanup if 30 seconds passed OR if cache is getting large
        if now - self._last_cleanup < 30 and len(self._used_codes) < 1000:
            return
        self._last_cleanup = now
        expired = [k for k, v in self._used_codes.items() if v < now]
        for k in expired:
            del self._used_codes[k]

    def check_and_mark(
        self,
        user_id: Union[int, str],
        code: str,
        purpose: Optional[str] = None,
        acting_user_id: Optional[Union[int, str]] = None,
    ) -> bool:
        """
        Check if code was already used and mark it as used.

        Security: the cache key includes both the user being authenticated
        and the actor presenting the code (so an admin presenting a 2FA
        code during impersonation cannot reuse a code that was meant for a
        different operation) and the declared purpose of the verification.

        Args:
            user_id: User ID the code is intended for
            code: The TOTP code
            purpose: Optional logical scope (e.g. "login", "set_2fa", "withdraw")
            acting_user_id: Optional ID of the principal presenting the code
        """
        purpose_part = f":p:{purpose or 'default'}"
        actor_part = f":a:{acting_user_id if acting_user_id is not None else user_id}"
        key = f"{user_id}:{code}{purpose_part}{actor_part}"
        now = int(time.time())

        # Prefer the shared cache so replay protection holds across workers.
        shared = self._shared_cache_claim(key, self._ttl)
        if shared is not None:
            return shared

        # No shared cache backend: fall back to the shared database so the
        # claim is still cross-worker on deployments without Valkey.
        db_claim = self._db_cache_claim(key, self._ttl)
        if db_claim is not None:
            return db_claim

        with self._lock:
            self._cleanup(now)

            if key in self._used_codes:
                return False  # Replay detected

            self._used_codes[key] = now + self._ttl
            return True


# Global replay cache instance
_replay_cache = TOTPReplayCache(ttl_seconds=90)


def set_replay_cache_db(db: Any) -> None:
    """Attach a shared database to the global replay cache.

    Called from the auth manager at setup so TOTP replay protection spans
    all workers even when no shared cache backend is configured.
    """
    _replay_cache.set_db(db)


def get_totp_config() -> Dict[str, Any]:
    """Get TOTP configuration from config system."""
    defaults = {
        "enabled": True,
        "issuer": "Plexichat",
        "digits": 6,
        "interval": 30,
        "backup_code_count": 10,
        "backup_code_length": 8,
        "backup_code_max_checks": 3,  # DoS mitigation
    }

    auth_config = config.get("authentication", {})
    user_totp_config = auth_config.get("totp", {})

    # Merge user config into defaults
    return {**defaults, **user_totp_config}


def generate_totp_secret() -> str:
    """
    Generate a new TOTP secret.

    Returns:
        Base32-encoded secret string
    """
    return pyotp.random_base32()


def encrypt_totp_secret(secret: str) -> str:
    """
    Encrypt a TOTP secret for database storage.

    Args:
        secret: Plain TOTP secret

    Returns:
        Encrypted secret string
    """
    return encrypt_data(secret)


def decrypt_totp_secret(encrypted_secret: str) -> str:
    """
    Decrypt a TOTP secret from database.

    Args:
        encrypted_secret: Encrypted secret string

    Returns:
        Plain TOTP secret
    """
    return decrypt_data(encrypted_secret)


def generate_totp_uri(secret: str, username: str, issuer: Optional[str] = None) -> str:
    """
    Generate a TOTP provisioning URI for QR codes.

    Args:
        secret: TOTP secret
        username: User's username or email
        issuer: App name (defaults to config)

    Returns:
        otpauth:// URI string
    """
    totp_config = get_totp_config()
    if issuer is None:
        issuer = totp_config.get("issuer", "Plexichat")

    totp = pyotp.TOTP(
        secret,
        digits=totp_config.get("digits", 6),
        interval=totp_config.get("interval", 30),
    )

    return totp.provisioning_uri(name=username, issuer_name=issuer)


def verify_totp_code(
    secret: str,
    code: str,
    window: int = 1,
    user_id: Optional[Union[int, str]] = None,
    purpose: Optional[str] = None,
    acting_user_id: Optional[Union[int, str]] = None,
) -> bool:
    """
    Verify a TOTP code with replay attack prevention.

    The replay cache is now keyed by ``(user_id, code, purpose, acting_user_id)``
    so a code presented for one purpose cannot be replayed against another
    (e.g. a ``login``-purpose code cannot be used to authorize a 2FA
    disable).  Callers MUST supply ``purpose`` and ``acting_user_id`` for
    any privileged operation; the legacy single-user ``user_id`` form is
    preserved for back-compat but is no longer the default.

    Args:
        secret: TOTP secret
        code: 6-digit code from authenticator app
        window: Number of time windows to check (for clock drift)
        user_id: User ID for replay prevention (if None, replay check is skipped)
        purpose: Logical scope of this code use (e.g. "login", "disable_2fa")
        acting_user_id: ID of the principal presenting the code

    Returns:
        True if code is valid and not a replay
    """
    if not isinstance(code, str) or not code.isdigit():
        return False

    totp_config = get_totp_config()

    totp = pyotp.TOTP(
        secret,
        digits=totp_config.get("digits", 6),
        interval=totp_config.get("interval", 30),
    )

    # First verify the code is cryptographically valid
    if not totp.verify(code, valid_window=window):
        return False

    # Then check for replay attack (if user_id provided)
    if user_id is not None:
        if not _replay_cache.check_and_mark(
            user_id, code, purpose=purpose, acting_user_id=acting_user_id
        ):
            return False  # Replay detected

    return True


def generate_backup_codes(count: Optional[int] = None) -> List[str]:
    """
    Generate backup codes for 2FA recovery.

    Args:
        count: Number of codes to generate (defaults to config)

    Returns:
        List of backup codes in XXXX-XXXX format
    """
    totp_config = get_totp_config()
    if count is None:
        count = int(totp_config.get("backup_code_count", 10))

    code_length = int(totp_config.get("backup_code_length", 8))
    # F3: enforce minimum entropy floor — 8 hex chars (32 bits) minimum;
    # lower values are clamped and warned so misconfiguration cannot weaken
    # backup codes below ~10/2^32 guessing resistance.
    if code_length < 8:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "backup_code_length %s below minimum 8 — clamping to 8", code_length
        )
        code_length = 8
    # Ensure even length for symmetric dash formatting and notify completeness
    if code_length % 2 != 0:
        code_length += 1
    half_length = code_length // 2

    codes = []
    for _ in range(count):
        # Generate random bytes and convert to hex
        random_bytes = os.urandom(half_length)
        code = random_bytes.hex().upper()
        # Format as XXXX-XXXX (length notification: each code is code_length chars plus dash)
        formatted = f"{code[:half_length]}-{code[half_length:]}"
        codes.append(formatted)

    return codes


def _hash_backup_code(code: str) -> str:
    """Fast hash a single backup code using SHA-256.

    Backup codes are high-entropy random tokens, so Argon2id is unnecessary
    overhead. SHA-256 provides adequate protection for storage.
    """
    normalized = code.replace("-", "").lower()
    return hashlib.sha256(normalized.encode()).hexdigest()


def hash_backup_codes(codes: List[str]) -> List[str]:
    """Hash backup codes for storage.

    Uses SHA-256 — backup codes are high-entropy random tokens, so
    expensive Argon2id hashing provides no meaningful security benefit.
    """
    return [_hash_backup_code(c) for c in codes]


def verify_backup_code(
    code: str, hashed_codes: List[Any], max_checks: Optional[int] = None
) -> Tuple[bool, int]:
    """Verify a backup code against stored hashes.

    Args:
        code: Backup code to verify (with or without dash)
        hashed_codes: List of hashed backup codes
        max_checks: Deprecated and ignored; all stored backup codes are checked

    Returns:
        Tuple of (valid, index) where index is the matched code index or -1
    """
    normalized = code.replace("-", "").lower()
    candidate = hashlib.sha256(normalized.encode()).hexdigest()

    for i, entry in enumerate(hashed_codes):
        if isinstance(entry, (list, tuple)) and len(entry) == 2:
            _, hashed = entry
        else:
            hashed = entry

        hashed_str = str(hashed) if not isinstance(hashed, str) else hashed

        if hmac.compare_digest(candidate, hashed_str):
            return True, i

    return False, -1


def generate_qr_code_data(uri: str) -> bytes:
    """
    Generate QR code image data for a TOTP URI.

    Args:
        uri: TOTP provisioning URI

    Returns:
        PNG image data as bytes
    """
    from io import BytesIO

    if qrcode is None:
        raise RuntimeError("qrcode is required to generate QR codes")

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(uri)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    buffer = BytesIO()
    img.save(buffer, "PNG")
    return buffer.getvalue()


def get_current_totp_code(secret: str) -> str:
    """
    Get the current TOTP code for a secret.
    Useful for testing.

    Args:
        secret: TOTP secret

    Returns:
        Current 6-digit code
    """
    totp_config = get_totp_config()

    totp = pyotp.TOTP(
        secret,
        digits=totp_config.get("digits", 6),
        interval=totp_config.get("interval", 30),
    )

    return totp.now()
