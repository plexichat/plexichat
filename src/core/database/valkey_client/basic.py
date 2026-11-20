import importlib
from typing import Any, List, Optional, cast

import utils.logger as logger

from .base import ValkeyClientBase, ValkeyOperationError, ValkeyValue, encode_scalar


class BasicMixin(ValkeyClientBase):
    """String keyspace commands.

    Every command has a blocking form (``set``/``get``) that uses the
    ``glide_sync`` handle and an awaitable form (``aset``/``aget``) that
    uses the ``glide`` handle. The ``a``-prefixed methods are the ones
    safe to call from the event loop.
    """

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
            self._client.set(full_key, encode_scalar(value), **kwargs)
            logger.debug(f"GLIDE SET: {key}")
            return True
        except Exception as e:
            logger.error(f"GLIDE SET failed for {key}: {e}")
            raise ValkeyOperationError(f"SET failed: {e}")

    def set_nx(self, key: str, value: ValkeyValue, ttl: Optional[int] = None) -> bool:
        """Set ``key`` only when it does not already exist (SET NX).

        Returns True when this caller won the claim, False when the key
        was already present. Used for single-winner claims such as TOTP
        replay protection, where a plain overwrite would accept a replay.
        """
        glide_sync = cast(Any, importlib.import_module("glide_sync"))

        self._ensure_connected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = self._expiry_kwargs(glide_sync, ttl)
            result = self._client.set(
                full_key,
                encode_scalar(value),
                conditional_set=glide_sync.ConditionalChange.ONLY_IF_DOES_NOT_EXIST,
                **kwargs,
            )
            claimed = result is not None
            logger.debug(f"GLIDE SET NX: {key} -> {'claimed' if claimed else 'exists'}")
            return claimed
        except Exception as e:
            logger.error(f"GLIDE SET NX failed for {key}: {e}")
            raise ValkeyOperationError(f"SET NX failed: {e}")

    async def aset(
        self, key: str, value: ValkeyValue, ttl: Optional[int] = None
    ) -> bool:
        glide = cast(Any, importlib.import_module("glide"))

        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = self._expiry_kwargs(glide, ttl)
            await aclient.set(full_key, encode_scalar(value), **kwargs)
            logger.debug(f"GLIDE SET (async): {key}")
            return True
        except Exception as e:
            logger.error(f"GLIDE SET (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"SET failed: {e}")

    async def aset_nx(
        self, key: str, value: ValkeyValue, ttl: Optional[int] = None
    ) -> bool:
        """Async counterpart of :meth:`set_nx`."""
        glide = cast(Any, importlib.import_module("glide"))

        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(self._sanitize_key(key))

        try:
            kwargs = self._expiry_kwargs(glide, ttl)
            result = await aclient.set(
                full_key,
                encode_scalar(value),
                conditional_set=glide.ConditionalChange.ONLY_IF_DOES_NOT_EXIST,
                **kwargs,
            )
            claimed = result is not None
            logger.debug(
                f"GLIDE SET NX (async): {key} -> {'claimed' if claimed else 'exists'}"
            )
            return claimed
        except Exception as e:
            logger.error(f"GLIDE SET NX (async) failed for {key}: {e}")
            raise ValkeyOperationError(f"SET NX failed: {e}")

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

    def mget(self, keys: List[str]) -> List[Optional[str]]:
        """Fetch many string keys in a single Valkey round trip.

        Batched counterpart of :meth:`get`: the returned list is positionally
        aligned with ``keys`` (``None`` marks a miss), so a caller replacing a
        ``for key in keys: get(key)`` loop keeps identical per-key semantics
        while paying one RTT instead of N. Prefixing and sanitisation are
        applied per key exactly as in :meth:`get`.

        Fail-closed: a driver/transport error raises
        :class:`ValkeyOperationError` instead of returning an all-``None``
        list, which a caller could mistake for "every key is absent" and fail
        open. An empty selection issues no round trip at all.
        """
        if not keys:
            return []
        self._ensure_connected()
        full_keys = [self._prefixed_key(self._sanitize_key(key)) for key in keys]

        try:
            values = self._client.mget(full_keys)
            result = self._decode_mget(values)
            found = sum(v is not None for v in result)
            logger.debug(f"GLIDE MGET: {len(keys)} keys, {found} found")
            return result
        except Exception as e:
            logger.error(f"GLIDE MGET failed: {e}")
            raise ValkeyOperationError(f"MGET failed: {e}")

    async def amget(self, keys: List[str]) -> List[Optional[str]]:
        """Async counterpart of :meth:`mget` — one round trip, fail-closed."""
        if not keys:
            return []
        aclient = self._ensure_aconnected()
        full_keys = [self._prefixed_key(self._sanitize_key(key)) for key in keys]

        try:
            values = await aclient.mget(full_keys)
            result = self._decode_mget(values)
            found = sum(v is not None for v in result)
            logger.debug(f"GLIDE MGET (async): {len(keys)} keys, {found} found")
            return result
        except Exception as e:
            logger.error(f"GLIDE MGET (async) failed: {e}")
            raise ValkeyOperationError(f"MGET failed: {e}")

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
