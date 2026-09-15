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

        The password is first pre-hashed as
        ``base64(HMAC-SHA256(pepper, password))`` (in software, or inside
        the HSM for ``hsm`` peppers) and the Argon2id digest of that value
        is stored in a versioned envelope::

            $plexichat$pepper-v1$pid=<id>$<argon2id-phc>

        Peppering is mandatory: when no pepper is available the call raises
        instead of producing an unpeppered hash. There is no legacy output
        format.
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
        raise ValueError(
            "password peppering is required but no pepper is available; "
            "check encryption.password_pepper configuration"
        )

    def verify_password(self, password: str, hash_str: str) -> bool:
        """Verify a password against a peppered Argon2id envelope.

        Anything that is not a well-formed pepper envelope fails closed —
        unpeppered hashes are not accepted under any configuration.
        """
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
            return False
        parsed = parse_envelope(hash_str)
        if parsed is None:
            return False
        pepper_id, inner = parsed
        try:
            store = get_pepper_store()
        except Exception:
            return False
        if store is None or not store.enabled:
            # Pepper unavailable: fail closed rather than guessing.
            return False
        # The hash's own pepper first (normal path), then current, then
        # every other known pepper as a fallback for rotation edge cases.
        ordered = [pepper_id]
        if store.current_id not in ordered:
            ordered.append(store.current_id)
        ordered.extend(pid for pid in store.provider_ids if pid not in ordered)
        candidates: list[str] = []
        for pid in ordered:
            try:
                candidates.append(store.prehash_with(pid, password))
            except Exception:
                continue
        if not candidates:
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

        Covers hashes pinned to a stale pepper id and Argon2 parameter
        changes (via ``PasswordHasher.check_needs_rehash`` when available).
        Non-envelope hashes fail closed at verify time, so there is nothing
        to upgrade them to and they report False here.
        """
        try:
            from ..pepper import get_pepper_store, parse_envelope
        except Exception:
            return False
        try:
            store = get_pepper_store()
        except Exception:
            return False
        if store is None or not store.enabled:
            return False
        parsed = parse_envelope(hash_str or "")
        if parsed is None:
            return False
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
