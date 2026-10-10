from typing import Any, List, Optional

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue, encode_scalar


class ListMixin(ValkeyClientBase):
    """List commands, in blocking and awaitable form."""

    @staticmethod
    def _encode_list_values(values: Any) -> List[Any]:
        return [encode_scalar(v) for v in values]

    def lpush(self, key: str, *values: ValkeyValue) -> int:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.lpush(full_key, self._encode_list_values(values))
        except Exception as e:
            logger.error(f"GLIDE LPUSH failed for {key}: {e}")
            raise ValkeyOperationError(f"LPUSH failed: {e}")

    async def alpush(self, key: str, *values: ValkeyValue) -> int:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return await aclient.lpush(full_key, self._encode_list_values(values))
        except Exception as e:
            logger.error(f"GLIDE LPUSH (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"LPUSH failed: {e}")

    def rpush(self, key: str, *values: ValkeyValue) -> int:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.rpush(full_key, self._encode_list_values(values))
        except Exception as e:
            logger.error(f"GLIDE RPUSH failed for {key}: {e}")
            raise ValkeyOperationError(f"RPUSH failed: {e}")

    async def arpush(self, key: str, *values: ValkeyValue) -> int:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return await aclient.rpush(full_key, self._encode_list_values(values))
        except Exception as e:
            logger.error(f"GLIDE RPUSH (async) failed for {key}: {e}")
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

    async def alpop(self, key: str) -> Optional[str]:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = await aclient.lpop(full_key)
            return self._decode(value)
        except Exception as e:
            logger.error(f"GLIDE LPOP (async) failed for {key}: {e}")
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

    async def arpop(self, key: str) -> Optional[str]:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = await aclient.rpop(full_key)
            return self._decode(value)
        except Exception as e:
            logger.error(f"GLIDE RPOP (async) failed for {key}: {e}")
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

    async def alrange(self, key: str, start: int, end: int) -> List[str]:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            raw = await aclient.lrange(full_key, start, end)
            return self._decode_list(raw)
        except Exception as e:
            logger.error(f"GLIDE LRANGE (async) failed for {key}: {e}")
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

    async def allen(self, key: str) -> int:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return int(await aclient.llen(full_key))
        except Exception as e:
            logger.error(f"GLIDE LLEN (async) failed for {key}: {e}")
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

    async def altrim(self, key: str, start: int, end: int) -> bool:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            await aclient.ltrim(full_key, start, end)
            logger.debug(f"GLIDE LTRIM (async): {key} ({start}, {end})")
            return True
        except Exception as e:
            logger.error(f"GLIDE LTRIM (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"LTRIM failed: {e}")
