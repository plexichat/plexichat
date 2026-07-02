"""Set operations mixin."""

from typing import Set, cast, Any

import utils.logger as logger

from .base import RedisClientBase, RedisOperationError, RedisValue


class SetMixin(RedisClientBase):
    """Mixin providing set operations."""

    def sadd(self, key: str, *values: RedisValue) -> int:
        """Add members to a set."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(client.sadd(full_key, *values))
        except Exception as e:
            logger.error(f"Redis SADD failed for {key}: {e}")
            raise RedisOperationError(f"SADD failed: {e}")

    def srem(self, key: str, *values: RedisValue) -> int:
        """Remove members from a set."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(client.srem(full_key, *values))
        except Exception as e:
            logger.error(f"Redis SREM failed for {key}: {e}")
            raise RedisOperationError(f"SREM failed: {e}")

    def smembers(self, key: str) -> Set[str]:
        """Get all members of a set."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return cast(Set[str], client.smembers(full_key))
        except Exception as e:
            logger.error(f"Redis SMEMBERS failed for {key}: {e}")
            raise RedisOperationError(f"SMEMBERS failed: {e}")

    def sismember(self, key: str, value: RedisValue) -> bool:
        """Check if value is a member of a set."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(client.sismember(full_key, cast(Any, value)))
        except Exception as e:
            logger.error(f"Redis SISMEMBER failed for {key}: {e}")
            raise RedisOperationError(f"SISMEMBER failed: {e}")
