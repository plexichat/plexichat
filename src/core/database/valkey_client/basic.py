from typing import Optional

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue


class BasicMixin(ValkeyClientBase):
    def set(self, key: str, value: ValkeyValue, ttl: Optional[int] = None) -> bool:
        from glide_sync import ExpirySet, ExpiryType  # pyright: ignore[reportMissingImports]

        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = {}
            if ttl is not None:
                kwargs["expiry"] = ExpirySet(ExpiryType.SEC, ttl)
            if isinstance(value, str):
                val: object = value.encode("utf-8") if isinstance(value, str) else value
            else:
                val = value
            self._client.set(full_key, val, **kwargs)
            logger.debug(f"GLIDE SET: {key}")
            return True
        except Exception as e:
            logger.error(f"GLIDE SET failed for {key}: {e}")
            raise ValkeyOperationError(f"SET failed: {e}")

    def set_bin(self, key: str, value: bytes, ttl: Optional[int] = None) -> bool:
        from glide_sync import ExpirySet, ExpiryType  # pyright: ignore[reportMissingImports]

        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = {}
            if ttl is not None:
                kwargs["expiry"] = ExpirySet(ExpiryType.SEC, ttl)
            self._client.set(full_key, value, **kwargs)
            logger.debug(f"GLIDE SET (binary): {key}")
            return True
        except Exception as e:
            logger.error(f"GLIDE SET (binary) failed for {key}: {e}")
            raise ValkeyOperationError(f"SET failed: {e}")

    def get(self, key: str) -> Optional[str]:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._client.get(full_key)
            result = self._decode(value)
            logger.debug(f"GLIDE GET: {key} -> {'found' if value else 'miss'}")
            return result
        except Exception as e:
            logger.error(f"GLIDE GET failed for {key}: {e}")
            raise ValkeyOperationError(f"GET failed: {e}")

    def get_bin(self, key: str) -> Optional[bytes]:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._client.get(full_key)
            logger.debug(f"GLIDE GET (binary): {key} -> {'found' if value else 'miss'}")
            return value
        except Exception as e:
            logger.error(f"GLIDE GET (binary) failed for {key}: {e}")
            raise ValkeyOperationError(f"GET failed: {e}")

    def delete(self, *keys: str) -> int:
        self._ensure_connected()
        full_keys = [self._prefixed_key(self._sanitize_key(k)) for k in keys]

        try:
            count = self._client.delete(full_keys)
            logger.debug(f"GLIDE DELETE: {len(keys)} keys, {count} deleted")
            return count
        except Exception as e:
            logger.error(f"GLIDE DELETE failed: {e}")
            raise ValkeyOperationError(f"DELETE failed: {e}")

    def exists(self, key: str) -> bool:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.exists([full_key]))
        except Exception as e:
            logger.error(f"GLIDE EXISTS failed for {key}: {e}")
            raise ValkeyOperationError(f"EXISTS failed: {e}")

    def expire(self, key: str, ttl: int) -> bool:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.expire(full_key, ttl))
        except Exception as e:
            logger.error(f"GLIDE EXPIRE failed for {key}: {e}")
            raise ValkeyOperationError(f"EXPIRE failed: {e}")

    def ttl(self, key: str) -> int:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.ttl(full_key)
        except Exception as e:
            logger.error(f"GLIDE TTL failed for {key}: {e}")
            raise ValkeyOperationError(f"TTL failed: {e}")
