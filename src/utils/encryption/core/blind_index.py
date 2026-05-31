"""
Blind index mixin - Keyed hashes for searching encrypted fields.

Part of the EncryptionManager composite class.
"""

import hashlib

from .protocol import EncryptionCoreProtocol

try:
    import xxhash

    XXHASH_AVAILABLE = True
except ImportError:
    xxhash = None
    XXHASH_AVAILABLE = False


class BlindIndexMixin(EncryptionCoreProtocol):
    """Mixin providing blind index generation for encrypted field search."""

    def blind_index(self, data: str, scope: str) -> str:
        """
        Generate a keyed hash for searching encrypted fields.

        Uses a dedicated root key for blind indexes to allow KEK rotation
        without breaking indexes.
        """
        root_key = self.keyring.blind_index_root_key or self.keyring._get_kek()
        index_key = hashlib.blake2b(
            root_key, key=scope.encode(), digest_size=32
        ).digest()
        return hashlib.blake2b(
            data.lower().strip().encode(), key=index_key, digest_size=32
        ).hexdigest()

    def fast_blind_index(self, data: str, scope: str) -> str:
        """Backwards-compatible alias for blind_index."""
        return self.blind_index(data, scope)

    def legacy_fast_blind_index(self, data: str, scope: str) -> str:
        """
        Legacy xxhash based blind index to support verifying older access tokens
        that haven't been regenerated yet.
        """
        if not XXHASH_AVAILABLE or xxhash is None:
            return ""

        kek = self.keyring._get_kek()
        seed_bytes = hashlib.blake2b(kek, key=scope.encode(), digest_size=8).digest()
        seed = int.from_bytes(seed_bytes, byteorder="big")

        return xxhash.xxh64(data.lower().strip().encode(), seed=seed).hexdigest()  # type: ignore[union-attr]
