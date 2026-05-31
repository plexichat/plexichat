"""Counter operations mixin."""

import utils.logger as logger

from .base import RedisClientBase, RedisOperationError


class CounterMixin(RedisClientBase):
    """Mixin providing counter increment/decrement operations."""

    def incr(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(client.incrby(full_key, amount))
        except Exception as e:
            logger.error(f"Redis INCR failed for {key}: {e}")
            raise RedisOperationError(f"INCR failed: {e}")

    def decr(self, key: str, amount: int = 1) -> int:
        """Decrement a counter."""
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(client.decrby(full_key, amount))
        except Exception as e:
            logger.error(f"Redis DECR failed for {key}: {e}")
            raise RedisOperationError(f"DECR failed: {e}")
