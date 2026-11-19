import asyncio
import importlib
import random
import secrets
import time
from typing import Any, Optional, cast

import utils.logger as logger

from .base import ValkeyClientBase

# NOTE: the leading/inner indentation below is load-bearing. GLIDE keys
# server-side script caching on the SHA1 of the source, and the test double
# in src/tests/test_valkey.py matches on that same hash, so reindenting
# this literal would silently change the script's identity.
_RELEASE_SCRIPT = """
        if redis.call("get", KEYS[1]) == ARGV[1] then
            return redis.call("del", KEYS[1])
        else
            return 0
        end
        """


# Contended-lock polling uses full-jitter exponential backoff. With a fixed
# poll interval every worker that began contending at the same instant stays
# phase-locked: once the holder releases, all N workers re-issue SET NX on the
# same tick, N-1 of them burn a round trip, and the whole herd resynchronises
# for the next tick. Jitter decorrelates the workers so aggregate load spreads
# across the interval instead of arriving in bursts; the exponential ramp
# (clamped to a ceiling) keeps an uncontended acquisition fast without hammering
# Valkey at a constant rate during a long stall. The ceiling equals the old
# fixed poll interval, so the worst-case per-worker rate never increases.
_INITIAL_BACKOFF_S = 0.005
_MAX_BACKOFF_S = 0.05


def _retry_delay(attempt: int, rng: Any = None) -> float:
    """Full-jitter exponential backoff for a failed lock acquisition.

    ``attempt`` is the zero-based index of the acquisition attempt that just
    failed. The delay is drawn uniformly from ``[0, ceiling]`` where
    ``ceiling = min(_MAX_BACKOFF_S, _INITIAL_BACKOFF_S * 2**attempt)``. ``rng``
    is injectable so tests can assert the exact schedule; production uses the
    module-level ``random``.
    """
    generator = rng if rng is not None else random
    # Cap the exponent so an absurd attempt count cannot overflow the float
    # conversion (the ceiling is clamped long before this matters).
    ceiling = min(_MAX_BACKOFF_S, _INITIAL_BACKOFF_S * (2 ** min(attempt, 32)))
    return float(generator.uniform(0.0, ceiling))


class LockMixin(ValkeyClientBase):
    """Distributed locks, in blocking and awaitable form.

    The awaitable form matters more than the blocking one on a serving
    loop: acquiring a contended lock spins, so ``aacquire_lock`` yields
    with ``asyncio.sleep`` between attempts instead of holding the loop
    for the whole timeout.
    """

    def acquire_lock(
        self, key: str, timeout: float = 10.0, lock_timeout: int = 30000
    ) -> Optional[str]:
        glide_sync = cast(Any, importlib.import_module("glide_sync"))
        ExpirySet = glide_sync.ExpirySet
        ExpiryType = glide_sync.ExpiryType
        ConditionalChange = glide_sync.ConditionalChange

        self._ensure_connected()
        client = self._client
        assert client is not None
        full_key = self._prefixed_key(f"lock:{self._sanitize_key(key)}")

        lock_token = secrets.token_hex(16)

        # A monotonic clock is used for the deadline: a wall-clock adjustment
        # (NTP step, manual change) mid-contention must not make a worker give
        # up early or spin far past its allotted window. The async twin uses
        # ``loop.time()`` for the same reason.
        start_time = time.monotonic()
        attempt = 0
        while time.monotonic() - start_time < timeout:
            result = client.set(
                full_key,
                lock_token,
                conditional_set=ConditionalChange.ONLY_IF_DOES_NOT_EXIST,
                expiry=ExpirySet(ExpiryType.MILLSEC, lock_timeout),
            )
            if result is not None:
                return lock_token
            time.sleep(_retry_delay(attempt))
            attempt += 1
        return None

    async def aacquire_lock(
        self, key: str, timeout: float = 10.0, lock_timeout: int = 30000
    ) -> Optional[str]:
        glide = cast(Any, importlib.import_module("glide"))
        ExpirySet = glide.ExpirySet
        ExpiryType = glide.ExpiryType
        ConditionalChange = glide.ConditionalChange

        aclient = self._ensure_aconnected()
        full_key = self._prefixed_key(f"lock:{self._sanitize_key(key)}")

        lock_token = secrets.token_hex(16)

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        attempt = 0
        while loop.time() < deadline:
            result = await aclient.set(
                full_key,
                lock_token,
                conditional_set=ConditionalChange.ONLY_IF_DOES_NOT_EXIST,
                expiry=ExpirySet(ExpiryType.MILLSEC, lock_timeout),
            )
            if result is not None:
                return lock_token
            await asyncio.sleep(_retry_delay(attempt))
            attempt += 1
        return None

    def release_lock(self, key: str, token: str) -> bool:
        self._ensure_connected()

        try:
            # ``eval_lua`` applies the key prefix itself, so pass the SAME
            # sanitized-but-unprefixed name acquire_lock used. The previous
            # code passed the raw (unsanitized) f"lock:{key}" here, so any
            # key needing sanitization was acquired under one name and
            # released under another — the lock was never actually freed.
            result = self.eval_lua(
                _RELEASE_SCRIPT, [f"lock:{self._sanitize_key(key)}"], [token]
            )
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to release lock for {key}: {e}")
            return False

    async def arelease_lock(self, key: str, token: str) -> bool:
        self._ensure_aconnected()

        try:
            result = await self.aeval_lua(
                _RELEASE_SCRIPT, [f"lock:{self._sanitize_key(key)}"], [token]
            )
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to release lock (async) for {key}: {e}")
            return False
