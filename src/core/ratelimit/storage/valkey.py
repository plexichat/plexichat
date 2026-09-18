"""
Valkey storage backend for rate limiting.
Distributed implementation for multi-instance deployments.
"""

import time
from typing import Optional, List, Dict, Any, Callable, cast

from src.core.database.valkey_client import (
    get_client,
    is_available,
    ValkeyError,
    ValkeyOperationError,
)
from .base import RateLimitStorage


# The token-bucket evaluation is one Lua script shared by the blocking and the
# awaitable path, so the two can never drift apart.
_TOKEN_BUCKET_LUA = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local cost = tonumber(ARGV[3])
        local ttl = tonumber(ARGV[4])
        local now = tonumber(ARGV[5])

        local bucket = redis.call('GET', key)
        local tokens
        local last_update

        if bucket then
            local data = cjson.decode(bucket)
            tokens = tonumber(data.tokens)
            last_update = tonumber(data.last_update)
        else
            tokens = capacity
            last_update = now
        end

        local elapsed = math.max(0, now - last_update)
        tokens = math.min(capacity, tokens + (elapsed * refill_rate))

        local allowed = false
        if tokens >= cost then
            tokens = tokens - cost
            allowed = true
        end

        local res = {
            tokens = tokens,
            last_update = now
        }
        
        redis.call('SET', key, cjson.encode(res), 'EX', ttl)
        
        local reset_after = 0
        if not allowed then
            reset_after = (cost - tokens) / refill_rate
        end
        
        return {tostring(allowed), tostring(math.floor(tokens)), tostring(reset_after)}
        """


def _decode_script_scalar(value: Any) -> Any:
    """Decode a value returned from a Lua script.

    GLIDE hands Lua bulk strings back as ``bytes``, while the fake client
    the unit suite uses returns ``str``. Comparing the raw result against
    a string literal therefore read as ``False`` on a real server -- every
    request was denied by a limiter that was working correctly -- and
    ``float(b"1.5")`` raised instead. Normalise before interpreting.
    """
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8")
    return value


def _script_truth(value: Any) -> bool:
    """Interpret a Lua boolean as the script serialised it (``tostring``)."""
    decoded = _decode_script_scalar(value)
    if isinstance(decoded, bool):
        return decoded
    return str(decoded).strip().lower() in {"true", "1"}


class ValkeyStorage(RateLimitStorage):
    """Distributed Valkey storage for rate limit buckets."""

    # F8: every bucket write carries a TTL so crafted distinct URLs expire
    # instead of accumulating forever.
    DEFAULT_BUCKET_TTL = 86400

    def __init__(self, key_prefix: str = "ratelimit"):
        """
        Initialize Valkey storage.

        Args:
            key_prefix: Prefix for all rate limit keys in Valkey.
        """
        self._key_prefix = key_prefix

    def _get_key(self, key: str) -> str:
        """Get full Valkey key."""
        return f"{self._key_prefix}:{key}"

    def get_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        """Get bucket state by key."""
        client = get_client()
        if not client or not is_available():
            return None

        full_key = self._get_key(key)
        try:
            return cast(Optional[Dict[str, Any]], client.get_json(full_key))
        except ValkeyOperationError:
            return None

    def set_bucket(
        self, key: str, state: Dict[str, Any], ttl: Optional[float] = None
    ) -> None:
        """Set bucket state."""
        client = get_client()
        if not client or not is_available():
            return

        full_key = self._get_key(key)
        try:
            # Valkey TTL is usually an int (seconds), but can be float in some clients (miliseconds)
            # ValkeyClient.set_json expects Optional[int]
            int_ttl = int(ttl) if ttl is not None else self.DEFAULT_BUCKET_TTL
            client.set_json(full_key, state, ttl=int_ttl)
        except ValkeyOperationError:
            pass

    def delete_bucket(self, key: str) -> bool:
        """Delete a bucket."""
        client = get_client()
        if not client or not is_available():
            return False

        full_key = self._get_key(key)
        try:
            return client.delete(full_key) > 0
        except ValkeyOperationError:
            return False

    def get_keys_by_prefix(self, prefix: str) -> List[str]:
        """Get all keys matching a prefix."""
        client = get_client()
        if not client or not is_available():
            return []

        # get_key adds self._key_prefix
        full_prefix = self._get_key(prefix)
        try:
            # client.keys returns keys relative to client.key_prefix
            keys = client.keys(f"{full_prefix}*")
            return keys
        except ValkeyOperationError:
            return []

    def delete_by_prefix(self, prefix: str) -> int:
        """Delete all keys matching a prefix."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_prefix = self._get_key(prefix)
        try:
            # client.keys returns keys relative to its own prefix
            keys = client.keys(f"{full_prefix}*")
            if keys:
                # ValkeyClient.delete expects keys WITHOUT its own prefix,
                # which is what client.keys returns.
                return client.delete(*keys)
            return 0
        except ValkeyOperationError:
            return 0

    def clear_all(self) -> None:
        """Clear all stored buckets."""
        self.delete_by_prefix("")

    def increment(self, key: str, field: str, amount: int = 1) -> int:
        """Atomically increment a field in a bucket."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_key = self._get_key(key)
        token = client.acquire_lock(full_key)
        if token:
            try:
                bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
                current = bucket.get(field, 0)
                new_value = current + amount
                bucket[field] = new_value
                client.set_json(full_key, bucket, ttl=self.DEFAULT_BUCKET_TTL)
                return new_value
            except ValkeyOperationError:
                return 0
            finally:
                if token:  # Re-check for type narrowing
                    client.release_lock(full_key, token)
        return 0

    def get_and_set(self, key: str, field: str, value: Any, default: Any = None) -> Any:
        """Atomically get current value and set new value."""
        client = get_client()
        if not client or not is_available():
            return default

        full_key = self._get_key(key)
        token = client.acquire_lock(full_key)
        if token:
            try:
                bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
                previous = bucket.get(field, default)
                bucket[field] = value
                client.set_json(full_key, bucket, ttl=self.DEFAULT_BUCKET_TTL)
                return previous
            except ValkeyOperationError:
                return default
            finally:
                if token:  # Re-check for type narrowing
                    client.release_lock(full_key, token)
        return default

    def add_to_list(
        self, key: str, field: str, value: Any, max_size: int = 1000
    ) -> int:
        """Add value to a list field, maintaining max size."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_key = self._get_key(key)
        token = client.acquire_lock(full_key)
        if token:
            try:
                bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
                lst = bucket.get(field, [])
                if not isinstance(lst, list):
                    lst = []
                lst.append(value)
                if len(lst) > max_size:
                    lst = lst[-max_size:]
                bucket[field] = lst
                client.set_json(full_key, bucket, ttl=self.DEFAULT_BUCKET_TTL)
                return len(lst)
            except ValkeyOperationError:
                return 0
            finally:
                if token:  # Re-check for type narrowing
                    client.release_lock(full_key, token)
        return 0

    def trim_list(self, key: str, field: str, min_value: Any) -> int:
        """Remove list items less than min_value."""
        client = get_client()
        if not client or not is_available():
            return 0

        full_key = self._get_key(key)
        token = client.acquire_lock(full_key)
        if token:
            try:
                bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
                lst = bucket.get(field, [])
                if not isinstance(lst, list):
                    return 0
                original_len = len(lst)
                lst = [v for v in lst if v >= min_value]
                bucket[field] = lst
                client.set_json(full_key, bucket, ttl=self.DEFAULT_BUCKET_TTL)
                return original_len - len(lst)
            except ValkeyOperationError:
                return 0
            finally:
                if token:  # Re-check for type narrowing
                    client.release_lock(full_key, token)
        return 0

    def acquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        """Acquire a lock for atomic operations."""
        client = get_client()
        if not client or not is_available():
            return None
        return client.acquire_lock(f"extlock:{key}", timeout=timeout)

    def release_lock(self, key: str, token: Optional[str] = None) -> None:
        """Release a lock."""
        client = get_client()
        if not client or not is_available() or token is None:
            return
        client.release_lock(f"extlock:{key}", token)

    def _backend_error_outcome(self, exc: Exception, capacity: int) -> tuple:
        """Outcome for an ``eval_token_bucket`` backend error (fail-closed).

        SECURITY: the previous ``except Exception: return True``
        FAIL-OPENED the bucket on backend errors. That turns a
        transient Valkey outage into an unbounded free-fire
        corridor for the attacker. We now FAIL-CLOSED: deny the
        request and surface the condition to the operator so
        it can be observed (rate-limit fail mode should never
        be silent in production). Operators who want the
        previous behaviour can opt in via configuration.
        """
        import os as _os
        import utils.config as _cfg

        try:
            fail_open = bool(
                _cfg.get("rate_limiting", {}).get("fail_open_on_backend_error", False)
            )
        except Exception:
            fail_open = False

        if fail_open and not _os.environ.get("PLEXICHAT_REQUIRE_FAIL_CLOSED", ""):
            from utils import logger as _log

            _log.error(
                "rate-limit Valkey backend error, FAIL-OPEN "
                "(insecure). Set "
                "rate_limiting.fail_open_on_backend_error=false "
                "or unset PLEXICHAT_REQUIRE_FAIL_CLOSED to keep "
                "fail-open behaviour: %s",
                exc,
            )
            return True, capacity, 0.0

        from utils import logger as _log

        _log.critical(
            "rate-limit Valkey backend error, FAIL-CLOSED (denying request): %s",
            exc,
        )
        return False, 0, 60.0

    def eval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        """Atomically evaluate a token bucket using Lua."""
        client = get_client()
        if not client or not is_available():
            # A configured distributed limiter must not silently become an
            # unlimited path when Valkey is unavailable. Deployments that
            # intentionally run without Valkey should select MemoryStorage
            # explicitly during setup.
            return False, 0, 60.0

        full_key = self._get_key(key)
        try:
            # We use time.time() here because monotonic() isn't shared across machines
            # and Valkey Lua scripts don't have access to non-deterministic TIME unless using specialized commands
            now = time.time()
            result = client.eval_lua(
                _TOKEN_BUCKET_LUA,
                keys=[full_key],
                args=[capacity, refill_rate, cost, ttl, now],
            )

            allowed = _script_truth(result[0])
            remaining = int(float(_decode_script_scalar(result[1])))
            reset_after = float(_decode_script_scalar(result[2]))

            return allowed, remaining, reset_after
        except Exception as exc:
            return self._backend_error_outcome(exc, capacity)

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------
    # These drive the event-loop (``glide``) handle natively rather than
    # inheriting :class:`RateLimitStorage`'s ``asyncio.to_thread`` offload,
    # so a request never parks a pool thread on a network round trip. The
    # blocking forms above are unchanged and stay correct for callers that
    # already run off the loop (threadpool handlers, CLI, tests).

    async def _alocked_update(
        self,
        key: str,
        mutate: Callable[[Dict[str, Any]], Any],
        default: Any,
    ) -> Any:
        """Read-modify-write ``key`` under the distributed lock (async).

        The lock is held across the whole get->mutate->set sequence, so two
        workers (or the blocking path) cannot lose each other's update.
        Returns ``default`` when the backend is unavailable or errors.
        """
        client = get_client()
        if not client or not is_available():
            return default

        full_key = self._get_key(key)
        token = await client.aacquire_lock(f"extlock:{key}", timeout=5.0)
        if not token:
            return default
        try:
            bucket = cast(Dict[str, Any], await client.aget_json(full_key) or {})
            result = mutate(bucket)
            await client.aset_json(full_key, bucket, ttl=self.DEFAULT_BUCKET_TTL)
            return result
        except ValkeyError:
            return default
        finally:
            await client.arelease_lock(f"extlock:{key}", token)

    async def aget_bucket(self, key: str) -> Optional[Dict[str, Any]]:
        client = get_client()
        if not client or not is_available():
            return None

        full_key = self._get_key(key)
        try:
            return cast(Optional[Dict[str, Any]], await client.aget_json(full_key))
        except ValkeyError:
            return None

    async def aset_bucket(
        self, key: str, state: Dict[str, Any], ttl: Optional[float] = None
    ) -> None:
        client = get_client()
        if not client or not is_available():
            return

        full_key = self._get_key(key)
        try:
            int_ttl = int(ttl) if ttl is not None else self.DEFAULT_BUCKET_TTL
            await client.aset_json(full_key, state, ttl=int_ttl)
        except ValkeyError:
            pass

    async def adelete_bucket(self, key: str) -> bool:
        client = get_client()
        if not client or not is_available():
            return False

        full_key = self._get_key(key)
        try:
            return await client.adelete(full_key) > 0
        except ValkeyError:
            return False

    async def aget_keys_by_prefix(self, prefix: str) -> List[str]:
        client = get_client()
        if not client or not is_available():
            return []

        full_prefix = self._get_key(prefix)
        try:
            return await client.akeys(f"{full_prefix}*")
        except ValkeyError:
            return []

    async def adelete_by_prefix(self, prefix: str) -> int:
        client = get_client()
        if not client or not is_available():
            return 0

        full_prefix = self._get_key(prefix)
        try:
            keys = await client.akeys(f"{full_prefix}*")
            if keys:
                return await client.adelete(*keys)
            return 0
        except ValkeyError:
            return 0

    async def aclear_all(self) -> None:
        await self.adelete_by_prefix("")

    async def aincrement(self, key: str, field: str, amount: int = 1) -> int:
        def _mutate(bucket: Dict[str, Any]) -> int:
            new_value = bucket.get(field, 0) + amount
            bucket[field] = new_value
            return int(new_value)

        return int(await self._alocked_update(key, _mutate, 0))

    async def aget_and_set(
        self, key: str, field: str, value: Any, default: Any = None
    ) -> Any:
        def _mutate(bucket: Dict[str, Any]) -> Any:
            previous = bucket.get(field, default)
            bucket[field] = value
            return previous

        return await self._alocked_update(key, _mutate, default)

    async def aadd_to_list(
        self, key: str, field: str, value: Any, max_size: int = 1000
    ) -> int:
        def _mutate(bucket: Dict[str, Any]) -> int:
            lst = bucket.get(field, [])
            if not isinstance(lst, list):
                lst = []
            lst.append(value)
            if len(lst) > max_size:
                lst = lst[-max_size:]
            bucket[field] = lst
            return len(lst)

        return int(await self._alocked_update(key, _mutate, 0))

    async def atrim_list(self, key: str, field: str, min_value: Any) -> int:
        def _mutate(bucket: Dict[str, Any]) -> int:
            lst = bucket.get(field, [])
            if not isinstance(lst, list):
                return 0
            original_len = len(lst)
            trimmed = [v for v in lst if v >= min_value]
            bucket[field] = trimmed
            return original_len - len(trimmed)

        return int(await self._alocked_update(key, _mutate, 0))

    async def aacquire_lock(self, key: str, timeout: float = 1.0) -> Optional[str]:
        client = get_client()
        if not client or not is_available():
            return None
        return await client.aacquire_lock(f"extlock:{key}", timeout=timeout)

    async def arelease_lock(self, key: str, token: Optional[str] = None) -> None:
        client = get_client()
        if not client or not is_available() or token is None:
            return
        await client.arelease_lock(f"extlock:{key}", token)

    async def aeval_token_bucket(
        self, key: str, capacity: int, refill_rate: float, cost: int, ttl: int = 86400
    ) -> tuple:
        client = get_client()
        if not client or not is_available():
            return False, 0, 60.0

        script = _TOKEN_BUCKET_LUA
        full_key = self._get_key(key)
        try:
            now = time.time()
            result = await client.aeval_lua(
                script, keys=[full_key], args=[capacity, refill_rate, cost, ttl, now]
            )
            allowed = _script_truth(result[0])
            remaining = int(float(_decode_script_scalar(result[1])))
            reset_after = float(_decode_script_scalar(result[2]))
            return allowed, remaining, reset_after
        except Exception as exc:
            return self._backend_error_outcome(exc, capacity)

    async def aupdate_bucket(
        self,
        key: str,
        mutate_fn: Callable[[Dict[str, Any]], None],
        ttl: Optional[float] = None,
    ) -> None:
        client = get_client()
        if not client or not is_available():
            return

        full_key = self._get_key(key)
        token = await self.aacquire_lock(key, timeout=5.0)
        try:
            state = cast(Dict[str, Any], await client.aget_json(full_key) or {})
            mutate_fn(state)
            int_ttl = int(ttl) if ttl is not None else self.DEFAULT_BUCKET_TTL
            await client.aset_json(full_key, state, ttl=int_ttl)
        except ValkeyError:
            pass
        finally:
            if token:
                await self.arelease_lock(key, token)

    async def aclose(self) -> None:
        """Valkey bucket state is server-side; nothing to close locally."""
        return None
