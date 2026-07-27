from typing import Set

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue


class SetMixin(ValkeyClientBase):
    def sadd(self, key: str, *values: ValkeyValue) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            encoded = [v.encode("utf-8") if isinstance(v, str) else v for v in values]
            return int(client.sadd(full_key, encoded))
        except Exception as e:
            logger.error(f"GLIDE SADD failed for {key}: {e}")
            raise ValkeyOperationError(f"SADD failed: {e}")

    def srem(self, key: str, *values: ValkeyValue) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            encoded = [v.encode("utf-8") if isinstance(v, str) else v for v in values]
            return int(client.srem(full_key, encoded))
        except Exception as e:
            logger.error(f"GLIDE SREM failed for {key}: {e}")
            raise ValkeyOperationError(f"SREM failed: {e}")

    def smembers(self, key: str) -> Set[str]:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            raw = client.smembers(full_key)
            return self._decode_set(raw)
        except Exception as e:
            logger.error(f"GLIDE SMEMBERS failed for {key}: {e}")
            raise ValkeyOperationError(f"SMEMBERS failed: {e}")

    def sismember(self, key: str, value: ValkeyValue) -> bool:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))
        val: object = value.encode("utf-8") if isinstance(value, str) else value

        try:
            return bool(client.sismember(full_key, val))
        except Exception as e:
            logger.error(f"GLIDE SISMEMBER failed for {key}: {e}")
            raise ValkeyOperationError(f"SISMEMBER failed: {e}")
