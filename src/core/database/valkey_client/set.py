from typing import Any, List, Set

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue


class SetMixin(ValkeyClientBase):
    """Set commands, in blocking and awaitable form."""

    @staticmethod
    def _encode_set_values(values: Any) -> List[Any]:
        return [v.encode("utf-8") if isinstance(v, str) else v for v in values]

    def sadd(self, key: str, *values: ValkeyValue) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(client.sadd(full_key, self._encode_set_values(values)))
        except Exception as e:
            logger.error(f"GLIDE SADD failed for {key}: {e}")
            raise ValkeyOperationError(f"SADD failed: {e}")

    async def asadd(self, key: str, *values: ValkeyValue) -> int:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(await aclient.sadd(full_key, self._encode_set_values(values)))
        except Exception as e:
            logger.error(f"GLIDE SADD (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"SADD failed: {e}")

    def srem(self, key: str, *values: ValkeyValue) -> int:
        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(client.srem(full_key, self._encode_set_values(values)))
        except Exception as e:
            logger.error(f"GLIDE SREM failed for {key}: {e}")
            raise ValkeyOperationError(f"SREM failed: {e}")

    async def asrem(self, key: str, *values: ValkeyValue) -> int:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(await aclient.srem(full_key, self._encode_set_values(values)))
        except Exception as e:
            logger.error(f"GLIDE SREM (async) failed for {key}: {e}")
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

    async def asmembers(self, key: str) -> Set[str]:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            raw = await aclient.smembers(full_key)
            return self._decode_set(raw)
        except Exception as e:
            logger.error(f"GLIDE SMEMBERS (async) failed for {key}: {e}")
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

    async def asismember(self, key: str, value: ValkeyValue) -> bool:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))
        val: object = value.encode("utf-8") if isinstance(value, str) else value

        try:
            return bool(await aclient.sismember(full_key, val))
        except Exception as e:
            logger.error(f"GLIDE SISMEMBER (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"SISMEMBER failed: {e}")
