from typing import List, Optional

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue


class ListMixin(ValkeyClientBase):
    def lpush(self, key: str, *values: ValkeyValue) -> int:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            encoded = [v.encode("utf-8") if isinstance(v, str) else v for v in values]
            return self._client.lpush(full_key, encoded)
        except Exception as e:
            logger.error(f"GLIDE LPUSH failed for {key}: {e}")
            raise ValkeyOperationError(f"LPUSH failed: {e}")

    def rpush(self, key: str, *values: ValkeyValue) -> int:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            encoded = [v.encode("utf-8") if isinstance(v, str) else v for v in values]
            return self._client.rpush(full_key, encoded)
        except Exception as e:
            logger.error(f"GLIDE RPUSH failed for {key}: {e}")
            raise ValkeyOperationError(f"RPUSH failed: {e}")

    def lpop(self, key: str) -> Optional[str]:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._client.lpop(full_key)
            return self._decode(value)
        except Exception as e:
            logger.error(f"GLIDE LPOP failed for {key}: {e}")
            raise ValkeyOperationError(f"LPOP failed: {e}")

    def rpop(self, key: str) -> Optional[str]:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._client.rpop(full_key)
            return self._decode(value)
        except Exception as e:
            logger.error(f"GLIDE RPOP failed for {key}: {e}")
            raise ValkeyOperationError(f"RPOP failed: {e}")

    def lrange(self, key: str, start: int, end: int) -> List[str]:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            raw = client.lrange(full_key, start, end)
            return self._decode_list(raw)
        except Exception as e:
            logger.error(f"GLIDE LRANGE failed for {key}: {e}")
            raise ValkeyOperationError(f"LRANGE failed: {e}")

    def llen(self, key: str) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(client.llen(full_key))
        except Exception as e:
            logger.error(f"GLIDE LLEN failed for {key}: {e}")
            raise ValkeyOperationError(f"LLEN failed: {e}")

    def ltrim(self, key: str, start: int, end: int) -> bool:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            client.ltrim(full_key, start, end)
            logger.debug(f"GLIDE LTRIM: {key} ({start}, {end})")
            return True
        except Exception as e:
            logger.error(f"GLIDE LTRIM failed for {key}: {e}")
            raise ValkeyOperationError(f"LTRIM failed: {e}")
