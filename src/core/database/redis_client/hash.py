"""Hash operations mixin."""

from typing import Dict

import utils.logger as logger

from .base import RedisClientBase, RedisOperationError, RedisValue


class HashMixin(RedisClientBase):
    """Mixin providing hash operations."""

    def hset(self, name: str, key: str, value: RedisValue) -> int:
        """Set a hash field."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            result = self._client.hset(full_name, key, value)
            logger.debug(f"Redis HSET: {name}.{key}")
            return result
        except Exception as e:
            logger.error(f"Redis HSET failed for {name}.{key}: {e}")
            raise RedisOperationError(f"HSET failed: {e}")

    def hget(self, name: str, key: str) -> str:
        """Get a hash field."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return self._client.hget(full_name, key)
        except Exception as e:
            logger.error(f"Redis HGET failed for {name}.{key}: {e}")
            raise RedisOperationError(f"HGET failed: {e}")

    def hgetall(self, name: str) -> Dict[str, str]:
        """Get all fields in a hash."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return self._client.hgetall(full_name)
        except Exception as e:
            logger.error(f"Redis HGETALL failed for {name}: {e}")
            raise RedisOperationError(f"HGETALL failed: {e}")

    def hdel(self, name: str, *keys: str) -> int:
        """Delete hash fields."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return self._client.hdel(full_name, *keys)
        except Exception as e:
            logger.error(f"Redis HDEL failed for {name}: {e}")
            raise RedisOperationError(f"HDEL failed: {e}")

    def hmset(self, name: str, mapping: Dict[str, RedisValue]) -> bool:
        """Set multiple hash fields."""
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            self._client.hset(full_name, mapping=mapping)
            logger.debug(f"Redis HMSET: {name} ({len(mapping)} fields)")
            return True
        except Exception as e:
            logger.error(f"Redis HMSET failed for {name}: {e}")
            raise RedisOperationError(f"HMSET failed: {e}")
