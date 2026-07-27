import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError


class CounterMixin(ValkeyClientBase):
    def incr(self, key: str, amount: int = 1) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            if amount == 1:
                return int(client.incr(full_key))
            return int(client.incrby(full_key, amount))
        except Exception as e:
            logger.error(f"GLIDE INCR failed for {key}: {e}")
            raise ValkeyOperationError(f"INCR failed: {e}")

    def decr(self, key: str, amount: int = 1) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            if amount == 1:
                return int(client.decr(full_key))
            return int(client.decrby(full_key, amount))
        except Exception as e:
            logger.error(f"GLIDE DECR failed for {key}: {e}")
            raise ValkeyOperationError(f"DECR failed: {e}")
