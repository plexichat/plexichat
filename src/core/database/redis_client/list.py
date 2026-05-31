"""List operations mixin."""

from typing import List, cast

import utils.logger as logger

from .base import RedisClientBase, RedisOperationError, RedisValue


class ListMixin(RedisClientBase):
    """Mixin providing list operations."""

    def lpush(self, key: str, *values: RedisValue) -> int:
        """Push values to the left of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.lpush(full_key, *values)
        except Exception as e:
            logger.error(f"Redis LPUSH failed for {key}: {e}")
            raise RedisOperationError(f"LPUSH failed: {e}")

    def rpush(self, key: str, *values: RedisValue) -> int:
        """Push values to the right of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.rpush(full_key, *values)
        except Exception as e:
            logger.error(f"Redis RPUSH failed for {key}: {e}")
            raise RedisOperationError(f"RPUSH failed: {e}")

    def lpop(self, key: str) -> str:
        """Pop from the left of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.lpop(full_key)
        except Exception as e:
            logger.error(f"Redis LPOP failed for {key}: {e}")
            raise RedisOperationError(f"LPOP failed: {e}")

    def rpop(self, key: str) -> str:
        """Pop from the right of a list."""
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.rpop(full_key)
        except Exception as e:
            logger.error(f"Redis RPOP failed for {key}: {e}")
            raise RedisOperationError(f"RPOP failed: {e}")

    def lrange(self, key: str, start: int, end: int) -> List[str]:
        """Get a range of elements from a list."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return cast(List[str], client.lrange(full_key, start, end))
        except Exception as e:
            logger.error(f"Redis LRANGE failed for {key}: {e}")
            raise RedisOperationError(f"LRANGE failed: {e}")

    def llen(self, key: str) -> int:
        """Get the length of a list."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(client.llen(full_key))
        except Exception as e:
            logger.error(f"Redis LLEN failed for {key}: {e}")
            raise RedisOperationError(f"LLEN failed: {e}")

    def ltrim(self, key: str, start: int, end: int) -> bool:
        """Trim a list to the specified range."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            client.ltrim(full_key, start, end)
            logger.debug(f"Redis LTRIM: {key} ({start}, {end})")
            return True
        except Exception as e:
            logger.error(f"Redis LTRIM failed for {key}: {e}")
            raise RedisOperationError(f"LTRIM failed: {e}")
