"""
Password hashing mixin - Argon2id password operations.

Part of the EncryptionManager composite class.
"""

import hashlib
import os
from typing import Optional, Tuple

from .protocol import EncryptionCoreProtocol


class PasswordMixin(EncryptionCoreProtocol):
    """Mixin providing password hashing and key derivation."""

    def derive_key(
        self,
        password: str,
        salt: Optional[bytes] = None,
        iterations: int = 100_000,
        length: int = 32,
    ) -> Tuple[bytes, bytes]:
        """Derive a key from a password using PBKDF2-HMAC-SHA256."""
        if not password:
            raise ValueError("Empty password")
        if salt is None:
            salt = os.urandom(16)
        if len(salt) < 16:
            raise ValueError("Salt too short")
        if length <= 0:
            raise ValueError("Invalid key length")

        key = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations), dklen=int(length)
        )
        return key, salt

    def hash_password(self, password: str) -> str:
        """Hash a password using peppered Argon2id.

        When a pepper store is enabled, the password is first pre-hashed as
        ``base64(HMAC-SHA256(pepper, password))`` (in software, or inside
        the HSM for ``hsm`` peppers) and the Argon2id digest of that value
        is stored in a versioned envelope::

            $plexichat$pepper-v1$pid=<id>$<argon2id-phc>

        With no pepper configured the behaviour is legacy plain Argon2id,
        so existing installs keep working until the operator enables a
        pepper (see ``encryption.password_pepper``).
        """
        if not password:
            raise ValueError("Empty password")
        try:
            from ..pepper import get_pepper_store
        except Exception:
            get_pepper_store = None  # type: ignore[assignment]
        store = None
        if get_pepper_store is not None:
            try:
                store = get_pepper_store()
            except Exception:
                store = None
        if store is not None and store.enabled:
            pepper_id, prehash = store.prehash_current(password)
            inner = self.password_hasher.hash(prehash)
            return f"$plexichat$pepper-v1$pid={pepper_id}${inner}"
        return self.password_hasher.hash(password)

    def verify_password(self, password: str, hash_str: str) -> bool:
        """Verify a password against a peppered or legacy Argon2id hash."""
        from ..core.manager import VerifyMismatchError, VerificationError, InvalidHash

        if not hash_str or not password:
            # Run the hasher anyway on empty passwords so timing does not
            # reveal which of the two was empty.
            try:
                self.password_hasher.verify(
                    self._dummy_hash_for_timing(), password or "x"
                )
            except Exception:
                pass
            return False
        try:
            from ..pepper import get_pepper_store, parse_envelope
        except Exception:
            get_pepper_store = None  # type: ignore[assignment]
            parse_envelope = None  # type: ignore[assignment]
        parsed = parse_envelope(hash_str) if parse_envelope else None
        if parsed is None:
            # Legacy unpeppered hash: verify directly. A successful verify
            # with peppering enabled is flagged by needs_rehash() so the
            # caller can upgrade the stored hash on login.
            try:
                self.password_hasher.verify(hash_str, password)
                return True
            except (VerifyMismatchError, VerificationError, InvalidHash):
                return False
            except Exception:
                return False
        pepper_id, inner = parsed
        store = None
        if get_pepper_store is not None:
            try:
                store = get_pepper_store()
            except Exception:
                store = None
        candidates: list[str] = []
        if store is not None and store.enabled:
            # The hash's own pepper first (normal path), then current (covers
            # a hash whose envelope was damaged but password is right — no,
            # keep it strict: only known ids), then every other known pepper
            # as a fallback for rotation edge cases.
            ordered = [pepper_id]
            if store.current_id not in ordered:
                ordered.append(store.current_id)
            ordered.extend(pid for pid in store.provider_ids if pid not in ordered)
            for pid in ordered:
                try:
                    candidates.append(store.prehash_with(pid, password))
                except Exception:
                    continue
            if not candidates:
                return False
        else:
            # Peppered hash but peppering now disabled/unavailable: fail
            # closed rather than guessing.
            return False
        for candidate in candidates:
            try:
                self.password_hasher.verify(inner, candidate)
                return True
            except (VerifyMismatchError, VerificationError, InvalidHash):
                continue
            except Exception:
                continue
        return False

    def needs_rehash(self, hash_str: str) -> bool:
        """True when a stored hash should be upgraded on next login.

        Covers: legacy unpeppered hashes (while peppering is enabled),
        hashes pinned to a stale pepper id, and Argon2 parameter changes
        (via ``PasswordHasher.check_needs_rehash`` when available).
        """
        try:
            from ..pepper import get_pepper_store, parse_envelope
        except Exception:
            return False
        try:
            store = get_pepper_store()
        except Exception:
            return False
        if store is not None and store.enabled:
            parsed = parse_envelope(hash_str or "")
            if parsed is None:
                return True
            pepper_id, inner = parsed
            if pepper_id != store.current_id:
                return True
            try:
                check = getattr(self.password_hasher, "check_needs_rehash", None)
                if callable(check) and check(inner):
                    return True
            except Exception:
                pass
            return False
        try:
            check = getattr(self.password_hasher, "check_needs_rehash", None)
            if callable(check):
                return bool(check(hash_str))
        except Exception:
            pass
        return False

    def get_password_pepper_id(self, hash_str: str) -> Optional[str]:
        """Return the pepper id for a peppered hash, else None."""
        try:
            from ..pepper import parse_envelope
        except Exception:
            return None
        parsed = parse_envelope(hash_str or "")
        return parsed[0] if parsed else None

    def _dummy_hash_for_timing(self) -> str:
        try:
            inner = self.password_hasher.hash("plexichat-timing-dummy")
            return inner
        except Exception:
            return "$argon2id$v=19$m=65536,t=3,p=2$dGVzdA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
