from typing import Dict, Optional

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue


class HashMixin(ValkeyClientBase):
    def hset(self, name: str, key: str, value: ValkeyValue) -> int:
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))
        val: object = value.encode("utf-8") if isinstance(value, str) else value

        try:
            result = self._client.hset(full_name, {key: val})
            logger.debug(f"GLIDE HSET: {name}.{key}")
            return result
        except Exception as e:
            logger.error(f"GLIDE HSET failed for {name}.{key}: {e}")
            raise ValkeyOperationError(f"HSET failed: {e}")

    def hget(self, name: str, key: str) -> Optional[str]:
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            value = self._client.hget(full_name, key)
            return self._decode(value)
        except Exception as e:
            logger.error(f"GLIDE HGET failed for {name}.{key}: {e}")
            raise ValkeyOperationError(f"HGET failed: {e}")

    def hgetall(self, name: str) -> Dict[str, str]:
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            raw = self._client.hgetall(full_name)
            return self._decode_dict(raw)
        except Exception as e:
            logger.error(f"GLIDE HGETALL failed for {name}: {e}")
            raise ValkeyOperationError(f"HGETALL failed: {e}")

    def hdel(self, name: str, *keys: str) -> int:
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return self._client.hdel(full_name, list(keys))
        except Exception as e:
            logger.error(f"GLIDE HDEL failed for {name}: {e}")
            raise ValkeyOperationError(f"HDEL failed: {e}")

    def hmset(self, name: str, mapping: Dict[str, ValkeyValue]) -> bool:
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))
        encoded = {
            k: v.encode("utf-8") if isinstance(v, str) else v
            for k, v in mapping.items()
        }

        try:
            self._client.hset(full_name, encoded)
            logger.debug(f"GLIDE HMSET: {name} ({len(mapping)} fields)")
            return True
        except Exception as e:
            logger.error(f"GLIDE HMSET failed for {name}: {e}")
            raise ValkeyOperationError(f"HMSET failed: {e}")
