"""Basic key-value operations mixin."""

from typing import Optional

import utils.logger as logger

from .base import RedisClientBase, RedisOperationError, RedisValue


class BasicMixin(RedisClientBase):
    """Mixin providing basic key-value operations."""

    def set(self, key: str, value: RedisValue, ttl: Optional[int] = None) -> bool:
        """
        Set a key-value pair.

        Args:
            key: The key name.
            value: The value to store.
            ttl: Time-to-live in seconds (optional).

        Returns:
            True if successful.
        """
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            if ttl:
                self._client.setex(full_key, ttl, value)
            else:
                self._client.set(full_key, value)
            logger.debug(f"Redis SET: {key}")
            return True
        except Exception as e:
            logger.error(f"Redis SET failed for {key}: {e}")
            raise RedisOperationError(f"SET failed: {e}")

    def set_bin(self, key: str, value: bytes, ttl: Optional[int] = None) -> bool:
        """
        Set a binary key-value pair.

        Args:
            key: The key name.
            value: The binary value to store.
            ttl: Time-to-live in seconds (optional).

        Returns:
            True if successful.
        """
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            if ttl:
                self._bin_client.setex(full_key, ttl, value)
            else:
                self._bin_client.set(full_key, value)
            logger.debug(f"Redis SET (binary): {key}")
            return True
        except Exception as e:
            logger.error(f"Redis SET (binary) failed for {key}: {e}")
            raise RedisOperationError(f"SET failed: {e}")

    def get(self, key: str) -> Optional[str]:
        """
        Get a value by key.

        Args:
            key: The key name.

        Returns:
            The value or None if not found.
        """
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._client.get(full_key)
            logger.debug(f"Redis GET: {key} -> {'found' if value else 'miss'}")
            return value
        except Exception as e:
            logger.error(f"Redis GET failed for {key}: {e}")
            raise RedisOperationError(f"GET failed: {e}")

    def get_bin(self, key: str) -> Optional[bytes]:
        """
        Get a binary value by key.

        Args:
            key: The key name.

        Returns:
            The binary value or None if not found.
        """
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._bin_client.get(full_key)
            logger.debug(f"Redis GET (binary): {key} -> {'found' if value else 'miss'}")
            return value
        except Exception as e:
            logger.error(f"Redis GET (binary) failed for {key}: {e}")
            raise RedisOperationError(f"GET failed: {e}")

    def delete(self, *keys: str) -> int:
        """
        Delete one or more keys.

        Args:
            keys: Key names to delete.

        Returns:
            Number of keys deleted.
        """
        self._ensure_connected()
        full_keys = [self._prefixed_key(self._sanitize_key(k)) for k in keys]

        try:
            count = self._client.delete(*full_keys)
            logger.debug(f"Redis DELETE: {len(keys)} keys, {count} deleted")
            return count
        except Exception as e:
            logger.error(f"Redis DELETE failed: {e}")
            raise RedisOperationError(f"DELETE failed: {e}")

    def exists(self, key: str) -> bool:
        """Check if a key exists."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.exists(full_key))
        except Exception as e:
            logger.error(f"Redis EXISTS failed for {key}: {e}")
            raise RedisOperationError(f"EXISTS failed: {e}")

    def expire(self, key: str, ttl: int) -> bool:
        """Set expiration on a key."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.expire(full_key, ttl))
        except Exception as e:
            logger.error(f"Redis EXPIRE failed for {key}: {e}")
            raise RedisOperationError(f"EXPIRE failed: {e}")

    def ttl(self, key: str) -> int:
        """Get remaining TTL for a key. Returns -1 if no TTL, -2 if key doesn't exist."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.ttl(full_key)
        except Exception as e:
            logger.error(f"Redis TTL failed for {key}: {e}")
            raise RedisOperationError(f"TTL failed: {e}")
