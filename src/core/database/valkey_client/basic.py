import importlib
from typing import Any, Optional, cast

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue


class BasicMixin(ValkeyClientBase):
    """String keyspace commands.

    Every command has a blocking form (``set``/``get``) that uses the
    ``glide_sync`` handle and an awaitable form (``aset``/``aget``) that
    uses the ``glide`` handle. The ``a``-prefixed methods are the ones
    safe to call from the event loop.
    """

    @staticmethod
    def _encode_scalar(value: ValkeyValue) -> object:
        """GLIDE takes bytes for text values; leave binary/numeric as-is."""
        return value.encode("utf-8") if isinstance(value, str) else value

    @staticmethod
    def _expiry_kwargs(glide_mod: Any, ttl: Optional[int]) -> dict:
        if ttl is None:
            return {}
        return {"expiry": glide_mod.ExpirySet(glide_mod.ExpiryType.SEC, ttl)}

    def set(self, key: str, value: ValkeyValue, ttl: Optional[int] = None) -> bool:
        glide_sync = cast(Any, importlib.import_module("glide_sync"))

        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = self._expiry_kwargs(glide_sync, ttl)
            self._client.set(full_key, self._encode_scalar(value), **kwargs)
            logger.debug(f"GLIDE SET: {key}")
            return True
        except Exception as e:
            logger.error(f"GLIDE SET failed for {key}: {e}")
            raise ValkeyOperationError(f"SET failed: {e}")

    async def aset(
        self, key: str, value: ValkeyValue, ttl: Optional[int] = None
    ) -> bool:
        glide = cast(Any, importlib.import_module("glide"))

        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = self._expiry_kwargs(glide, ttl)
            await aclient.set(full_key, self._encode_scalar(value), **kwargs)
            logger.debug(f"GLIDE SET (async): {key}")
            return True
        except Exception as e:
            logger.error(f"GLIDE SET (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"SET failed: {e}")

    def set_bin(self, key: str, value: bytes, ttl: Optional[int] = None) -> bool:
        glide_sync = cast(Any, importlib.import_module("glide_sync"))

        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = self._expiry_kwargs(glide_sync, ttl)
            self._client.set(full_key, value, **kwargs)
            logger.debug(f"GLIDE SET (binary): {key}")
            return True
        except Exception as e:
            logger.error(f"GLIDE SET (binary) failed for {key}: {e}")
            raise ValkeyOperationError(f"SET failed: {e}")

    async def aset_bin(self, key: str, value: bytes, ttl: Optional[int] = None) -> bool:
        glide = cast(Any, importlib.import_module("glide"))

        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = self._expiry_kwargs(glide, ttl)
            await aclient.set(full_key, value, **kwargs)
            logger.debug(f"GLIDE SET (binary, async): {key}")
            return True
        except Exception as e:
            logger.error(f"GLIDE SET (binary, async) failed for {key}: {e}")
            raise ValkeyOperationError(f"SET failed: {e}")

    def get(self, key: str) -> Optional[str]:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._client.get(full_key)
            result = self._decode(value)
            logger.debug(f"GLIDE GET: {key} -> {'found' if value else 'miss'}")
            return result
        except Exception as e:
            logger.error(f"GLIDE GET failed for {key}: {e}")
            raise ValkeyOperationError(f"GET failed: {e}")

    async def aget(self, key: str) -> Optional[str]:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = await aclient.get(full_key)
            result = self._decode(value)
            logger.debug(f"GLIDE GET (async): {key} -> {'found' if value else 'miss'}")
            return result
        except Exception as e:
            logger.error(f"GLIDE GET (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"GET failed: {e}")

    def get_bin(self, key: str) -> Optional[bytes]:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = self._client.get(full_key)
            logger.debug(f"GLIDE GET (binary): {key} -> {'found' if value else 'miss'}")
            return value
        except Exception as e:
            logger.error(f"GLIDE GET (binary) failed for {key}: {e}")
            raise ValkeyOperationError(f"GET failed: {e}")

    async def aget_bin(self, key: str) -> Optional[bytes]:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            value = await aclient.get(full_key)
            logger.debug(
                f"GLIDE GET (binary, async): {key} -> {'found' if value else 'miss'}"
            )
            return value
        except Exception as e:
            logger.error(f"GLIDE GET (binary, async) failed for {key}: {e}")
            raise ValkeyOperationError(f"GET failed: {e}")

    def delete(self, *keys: str) -> int:
        self._ensure_connected()
        full_keys = [self._prefixed_key(self._sanitize_key(k)) for k in keys]

        try:
            count = self._client.delete(full_keys)
            logger.debug(f"GLIDE DELETE: {len(keys)} keys, {count} deleted")
            return count
        except Exception as e:
            logger.error(f"GLIDE DELETE failed: {e}")
            raise ValkeyOperationError(f"DELETE failed: {e}")

    async def adelete(self, *keys: str) -> int:
        aclient = self._ensure_aconnected()
        full_keys = [self._prefixed_key(self._sanitize_key(k)) for k in keys]

        try:
            count = await aclient.delete(full_keys)
            logger.debug(f"GLIDE DELETE (async): {len(keys)} keys, {count} deleted")
            return count
        except Exception as e:
            logger.error(f"GLIDE DELETE (async) failed: {e}")
            raise ValkeyOperationError(f"DELETE failed: {e}")

    def exists(self, key: str) -> bool:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.exists([full_key]))
        except Exception as e:
            logger.error(f"GLIDE EXISTS failed for {key}: {e}")
            raise ValkeyOperationError(f"EXISTS failed: {e}")

    async def aexists(self, key: str) -> bool:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(await aclient.exists([full_key]))
        except Exception as e:
            logger.error(f"GLIDE EXISTS (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"EXISTS failed: {e}")

    def expire(self, key: str, ttl: int) -> bool:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(self._client.expire(full_key, ttl))
        except Exception as e:
            logger.error(f"GLIDE EXPIRE failed for {key}: {e}")
            raise ValkeyOperationError(f"EXPIRE failed: {e}")

    async def aexpire(self, key: str, ttl: int) -> bool:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return bool(await aclient.expire(full_key, ttl))
        except Exception as e:
            logger.error(f"GLIDE EXPIRE (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"EXPIRE failed: {e}")

    def ttl(self, key: str) -> int:
        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return self._client.ttl(full_key)
        except Exception as e:
            logger.error(f"GLIDE TTL failed for {key}: {e}")
            raise ValkeyOperationError(f"TTL failed: {e}")

    async def attl(self, key: str) -> int:
        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            return await aclient.ttl(full_key)
        except Exception as e:
            logger.error(f"GLIDE TTL (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"TTL failed: {e}")
