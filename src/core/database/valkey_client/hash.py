from typing import Any, Dict, Optional

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue, encode_scalar


class HashMixin(ValkeyClientBase):
    """Hash commands, in blocking and awaitable form."""

    @staticmethod
    def _encode_mapping(mapping: Dict[str, ValkeyValue]) -> Dict[str, Any]:
        return {k: encode_scalar(v) for k, v in mapping.items()}

    def hset(self, name: str, key: str, value: ValkeyValue) -> int:
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))
        val = encode_scalar(value)

        try:
            result = self._client.hset(full_name, {key: val})
            logger.debug(f"GLIDE HSET: {name}.{key}")
            return result
        except Exception as e:
            logger.error(f"GLIDE HSET failed for {name}.{key}: {e}")
            raise ValkeyOperationError(f"HSET failed: {e}")

    async def ahset(self, name: str, key: str, value: ValkeyValue) -> int:
        aclient = self._ensure_aconnected()
        full_name = self._prefixed_key(self._sanitize_key(name))
        val = encode_scalar(value)

        try:
            result = await aclient.hset(full_name, {key: val})
            logger.debug(f"GLIDE HSET (async): {name}.{key}")
            return result
        except Exception as e:
            logger.error(f"GLIDE HSET (async) failed for {name}.{key}: {e}")
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

    async def ahget(self, name: str, key: str) -> Optional[str]:
        aclient = self._ensure_aconnected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            value = await aclient.hget(full_name, key)
            return self._decode(value)
        except Exception as e:
            logger.error(f"GLIDE HGET (async) failed for {name}.{key}: {e}")
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

    async def ahgetall(self, name: str) -> Dict[str, str]:
        aclient = self._ensure_aconnected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            raw = await aclient.hgetall(full_name)
            return self._decode_dict(raw)
        except Exception as e:
            logger.error(f"GLIDE HGETALL (async) failed for {name}: {e}")
            raise ValkeyOperationError(f"HGETALL failed: {e}")

    def hdel(self, name: str, *keys: str) -> int:
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return self._client.hdel(full_name, list(keys))
        except Exception as e:
            logger.error(f"GLIDE HDEL failed for {name}: {e}")
            raise ValkeyOperationError(f"HDEL failed: {e}")

    async def ahdel(self, name: str, *keys: str) -> int:
        aclient = self._ensure_aconnected()
        full_name = self._prefixed_key(self._sanitize_key(name))

        try:
            return await aclient.hdel(full_name, list(keys))
        except Exception as e:
            logger.error(f"GLIDE HDEL (async) failed for {name}: {e}")
            raise ValkeyOperationError(f"HDEL failed: {e}")

    def hmset(self, name: str, mapping: Dict[str, ValkeyValue]) -> bool:
        self._ensure_connected()
        full_name = self._prefixed_key(self._sanitize_key(name))
        encoded = self._encode_mapping(mapping)

        try:
            self._client.hset(full_name, encoded)
            logger.debug(f"GLIDE HMSET: {name} ({len(mapping)} fields)")
            return True
        except Exception as e:
            logger.error(f"GLIDE HMSET failed for {name}: {e}")
            raise ValkeyOperationError(f"HMSET failed: {e}")

    async def ahmset(self, name: str, mapping: Dict[str, ValkeyValue]) -> bool:
        aclient = self._ensure_aconnected()
        full_name = self._prefixed_key(self._sanitize_key(name))
        encoded = self._encode_mapping(mapping)

        try:
            await aclient.hset(full_name, encoded)
            logger.debug(f"GLIDE HMSET (async): {name} ({len(mapping)} fields)")
            return True
        except Exception as e:
            logger.error(f"GLIDE HMSET (async) failed for {name}: {e}")
            raise ValkeyOperationError(f"HMSET failed: {e}")
