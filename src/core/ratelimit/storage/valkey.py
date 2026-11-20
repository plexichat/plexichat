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
from ..exceptions import RateLimitStoreUnavailable
from .base import RateLimitStorage


# The token-bucket evaluation is one Lua script shared by the blocking and the
# awaitable path, so the two can never drift apart.
#
# SECURITY: the script used to rebuild the bucket from scratch as
# ``{tokens, last_update}`` and SET that over the stored JSON. A bucket also
# carries the hourly/daily quota counters written by ``update_bucket`` (see
# ``manager.algorithms.apply_secondary_limits``), so EVERY token spend wiped
# them. With Valkey as the backend the hourly/daily limit therefore never
# accumulated past one request — a distributed-only quota bypass. Decode the
# stored value into ``state`` and write that SAME table back so unrelated
# fields survive a spend, matching MemoryStorage and DatabaseStorage.
_TOKEN_BUCKET_LUA = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local cost = tonumber(ARGV[3])
        local ttl = tonumber(ARGV[4])
        local now = tonumber(ARGV[5])

        local state = {}
        local tokens = capacity
        local last_update = now

        local bucket = redis.call('GET', key)
        if bucket then
            local ok, decoded = pcall(cjson.decode, bucket)
            if ok and type(decoded) == 'table' then
                state = decoded
                if tonumber(state.tokens) then
                    tokens = tonumber(state.tokens)
                end
                if tonumber(state.last_update) then
                    last_update = tonumber(state.last_update)
                end
            end
        end

        local elapsed = math.max(0, now - last_update)
        tokens = math.min(capacity, tokens + (elapsed * refill_rate))

        local allowed = false
        if tokens >= cost then
            tokens = tokens - cost
            allowed = true
        end

        state.tokens = tokens
        state.last_update = now
        redis.call('SET', key, cjson.encode(state), 'EX', ttl)

        local reset_after = 0
        if not allowed then
            reset_after = (cost - tokens) / refill_rate
        end

        return {tostring(allowed), tostring(math.floor(tokens)), tostring(reset_after)}
        """

# HOT PATH: a token-bucket check that also carries hourly/daily secondary
# quotas used to run TWO locked round trips on the same bucket:
#   eval_token_bucket  -> lock, GET+spend+SET (Lua), unlock
#   update_bucket      -> lock, GET+secondary-mutate+SET, unlock
# i.e. seven client round trips, all serialising on the same ``extlock:<key>``.
# Both operations read and write the SAME JSON state, so the second GET is
# redundant. This script fuses them into ONE atomic server-side execution:
#   lock, GET+spend+secondary+SET (Lua), unlock  -> three round trips.
#
# Bounded + multi-node safe:
#   * one key, one script, the same bounded TTL as the plain spend;
#   * the SAME distributed lock is still held across the whole operation, so
#     this never interleaves with ``increment``/``get_and_set``/``add_to_list``
#     (they would otherwise clobber a field via their own locked SET);
#   * server-side atomicity means every node sees the same counter value.
# The hourly/daily maths below is a faithful port of
# ``manager.algorithms.check_hourly_limit`` / ``check_daily_limit`` /
# ``apply_secondary_limits``. Any backend error still fails closed (see
# ``_backend_error_outcome``); the final ``'1'`` is the "mutation applied"
# proof the caller uses to reject a silently-skipped write.
_TOKEN_BUCKET_WITH_SECONDARY_LUA = """
        local key = KEYS[1]
        local capacity = tonumber(ARGV[1])
        local refill_rate = tonumber(ARGV[2])
        local cost = tonumber(ARGV[3])
        local ttl = tonumber(ARGV[4])
        local now = tonumber(ARGV[5])
        local hourly_limit = tonumber(ARGV[6]) or 0
        local daily_limit = tonumber(ARGV[7]) or 0
        local effective_limit = tonumber(ARGV[8]) or capacity

        local state = {}
        local tokens = capacity
        local last_update = now
        local bucket = redis.call('GET', key)
        if bucket then
            local ok, decoded = pcall(cjson.decode, bucket)
            if ok and type(decoded) == 'table' then
                state = decoded
                if tonumber(state.tokens) then
                    tokens = tonumber(state.tokens)
                end
                if tonumber(state.last_update) then
                    last_update = tonumber(state.last_update)
                end
            end
        end

        local elapsed = math.max(0, now - last_update)
        tokens = math.min(capacity, tokens + (elapsed * refill_rate))

        local allowed = false
        if tokens >= cost then
            tokens = tokens - cost
            allowed = true
        end
        -- ``remaining`` is reported from the post-spend value, exactly as the
        -- two-call path did (the secondary rollback never changed it).
        local remaining = math.floor(tokens)

        local secondary_reset_after = nil

        if allowed and hourly_limit > 0 then
            local hourly_reset = tonumber(state.hourly_reset)
            if not hourly_reset then
                hourly_reset = now + 3600
            end
            local hourly_count = tonumber(state.hourly_count) or 0
            if now >= hourly_reset then
                hourly_reset = now + 3600
                hourly_count = 0
            end
            if hourly_count + cost <= hourly_limit then
                hourly_count = hourly_count + cost
            else
                secondary_reset_after = hourly_reset - now
            end
            state.hourly_reset = hourly_reset
            state.hourly_count = hourly_count
        end

        if allowed and secondary_reset_after == nil and daily_limit > 0 then
            local daily_reset = tonumber(state.daily_reset)
            if not daily_reset then
                daily_reset = now + 86400
            end
            local daily_count = tonumber(state.daily_count) or 0
            if now >= daily_reset then
                daily_reset = now + 86400
                daily_count = 0
            end
            if daily_count + cost <= daily_limit then
                daily_count = daily_count + cost
            else
                secondary_reset_after = daily_reset - now
            end
            state.daily_reset = daily_reset
            state.daily_count = daily_count
        end

        local reset_after = 0
        if not allowed then
            reset_after = (cost - tokens) / refill_rate
        elseif secondary_reset_after ~= nil then
            allowed = false
            reset_after = secondary_reset_after
            -- Roll the already-spent primary token back so a near-cap request
            -- does not burn quota without succeeding (matches Python).
            tokens = math.min(effective_limit, tokens + cost)
        end

        state.tokens = tokens
        state.last_update = now
        redis.call('SET', key, cjson.encode(state), 'EX', ttl)

        return {tostring(allowed), tostring(remaining), tostring(reset_after), '1'}
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
    # The TTL alone does not bound the KEYSpace: a client that rotates a
    # caller-influenced identifier (IP, route, resource id) can mint many
    # distinct keys inside one TTL window and grow the shared store without
    # limit. MemoryStorage (max_buckets) and DatabaseStorage (MAX_BUCKET_ROWS)
    # both cap their state; Valkey had no equivalent. Keep the same order of
    # magnitude so a bounded distributed store cannot be inflated at will.
    MAX_BUCKET_KEYS = 50000
    # Enforcing the cap requires a cursor-based SCAN of the keyspace, so it is
    # throttled by TIME, not per N writes: at high request rates a scan every
    # 10 writes would mean hundreds of round trips per second. Sixty seconds
    # matches MemoryStorage's default cleanup cadence and keeps the hot path
    # free of scans in the common case.
    _CAP_CHECK_INTERVAL = 60.0
    # Every distributed-lock acquisition in this module is explicitly bounded.
    # The token-bucket paths already use 5.0. The four public mutators used to
    # call ``acquire_lock`` with no timeout at all, inheriting the client's
    # implicit default (10s) -- inconsistent with the rest of the module and
    # unbounded at the call site. Pin every mutator to the same finite window
    # so a stalled/contended lock can never park a worker indefinitely.
    _MUTATOR_LOCK_TIMEOUT = 5.0

    def __init__(self, key_prefix: str = "ratelimit"):
        """
        Initialize Valkey storage.

        Args:
            key_prefix: Prefix for all rate limit keys in Valkey.
        """
        self._key_prefix = key_prefix
        self._last_cap_check = 0.0

    def _get_key(self, key: str) -> str:
        """Get full Valkey key."""
        return f"{self._key_prefix}:{key}"

    def _maybe_enforce_key_cap(self, client: Any) -> None:
        """Best-effort pruning of the data keyspace (blocking).

        Scans only the bucket namespace owned by ``_key_prefix`` (never the
        ``lock:`` namespace the distributed locks live in), so it uses exactly
        the same keys as every writer — no drift between "key we write" and
        "key we prune". Soonest-expiring keys are evicted first. Never raises:
        a pruning failure must not break the rate-limit decision itself.
        """
        now = time.monotonic()
        if now - getattr(self, "_last_cap_check", 0.0) < self._CAP_CHECK_INTERVAL:
            return
        self._last_cap_check = now
        try:
            keys = client.keys(f"{self._key_prefix}:*")
            if len(keys) <= self.MAX_BUCKET_KEYS:
                return
            scored = []
            for key in keys:
                try:
                    scored.append((int(client.ttl(key)), key))
                except Exception:
                    scored.append((-1, key))
            # Unknown TTL (-1/-2) sorts last: prefer keys we know expire soon.
            scored.sort(key=lambda pair: (pair[0] if pair[0] >= 0 else 10**9, pair[1]))
            excess = len(keys) - self.MAX_BUCKET_KEYS
            victims = [key for _, key in scored[:excess]]
            if victims:
                client.delete(*victims)
        except Exception as prune_error:
            import utils.logger as logger

            logger.debug(f"rate limit Valkey key cap prune failed: {prune_error}")

    async def _amaybe_enforce_key_cap(self, client: Any) -> None:
        """Awaitable counterpart of :meth:`_maybe_enforce_key_cap`."""
        now = time.monotonic()
        if now - getattr(self, "_last_cap_check", 0.0) < self._CAP_CHECK_INTERVAL:
            return
        self._last_cap_check = now
        try:
            keys = await client.akeys(f"{self._key_prefix}:*")
            if len(keys) <= self.MAX_BUCKET_KEYS:
                return
            scored = []
            for key in keys:
                try:
                    scored.append((int(await client.attl(key)), key))
                except Exception:
                    scored.append((-1, key))
            scored.sort(key=lambda pair: (pair[0] if pair[0] >= 0 else 10**9, pair[1]))
            excess = len(keys) - self.MAX_BUCKET_KEYS
            victims = [key for _, key in scored[:excess]]
            if victims:
                await client.adelete(*victims)
        except Exception as prune_error:
            import utils.logger as logger

            logger.debug(f"rate limit Valkey key cap prune failed: {prune_error}")

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
            self._maybe_enforce_key_cap(client)
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

    def _locked_update(
        self,
        key: str,
        mutate: Callable[[Dict[str, Any]], Any],
        default: Any,
    ) -> Any:
        """Read-modify-write ``key`` under the distributed lock (blocking).

        Fail-closed counterpart of :meth:`_alocked_update`. The four public
        mutators must NOT return ``default`` when the lock cannot be acquired:
        that silently drops the read-modify-write, so an abuse counter (upload
        bytes, sticker/friend-request quotas, ...) under-counts and multiplies
        an attacker's budget by the number of contending workers. Refuse the
        operation instead, letting callers fail closed.

        The lock name matches the async mutators, the public lock helpers and
        the token-bucket path (``extlock:<key>``), so every writer serialises
        on the same lock. Data itself stays under ``full_key``.
        """
        client = get_client()
        if not client or not is_available():
            return default

        full_key = self._get_key(key)
        lock_key = f"extlock:{key}"
        # Explicit, finite timeout: never depend on the client's default.
        token = client.acquire_lock(lock_key, timeout=self._MUTATOR_LOCK_TIMEOUT)
        if not token:
            raise RateLimitStoreUnavailable(
                f"could not acquire distributed rate-limit lock for {key!r}"
            )
        try:
            bucket = cast(Dict[str, Any], client.get_json(full_key) or {})
            result = mutate(bucket)
            client.set_json(full_key, bucket, ttl=self.DEFAULT_BUCKET_TTL)
        except ValkeyOperationError:
            return default
        finally:
            # The client verifies the token before deleting the lock key, so
            # a late holder cannot free a lock another worker now owns.
            client.release_lock(lock_key, token)
        # KEY-CAP PRUNE OUTSIDE THE LOCK. The prune is an O(N) SCAN plus one TTL
        # round trip per key. Running it while holding ``extlock:<key>`` stretched
        # the critical section to the whole keyspace, so a slow prune could
        # outlive the lock's TTL and let a second worker acquire the same lock
        # and clobber this write (fail-open double spend). Prune only once the
        # lock is gone; the decision stays best-effort and every bucket still
        # carries a TTL, so skipping it on an error path is safe.
        self._maybe_enforce_key_cap(client)
        return result

    def increment(self, key: str, field: str, amount: int = 1) -> int:
        """Atomically increment a field in a bucket."""

        def _mutate(bucket: Dict[str, Any]) -> int:
            new_value = bucket.get(field, 0) + amount
            bucket[field] = new_value
            return int(new_value)

        return int(self._locked_update(key, _mutate, 0))

    def get_and_set(self, key: str, field: str, value: Any, default: Any = None) -> Any:
        """Atomically get current value and set new value."""

        def _mutate(bucket: Dict[str, Any]) -> Any:
            previous = bucket.get(field, default)
            bucket[field] = value
            return previous

        return self._locked_update(key, _mutate, default)

    def add_to_list(
        self, key: str, field: str, value: Any, max_size: int = 1000
    ) -> int:
        """Add value to a list field, maintaining max size."""

        def _mutate(bucket: Dict[str, Any]) -> int:
            lst = bucket.get(field, [])
            if not isinstance(lst, list):
                lst = []
            lst.append(value)
            if len(lst) > max_size:
                lst = lst[-max_size:]
            bucket[field] = lst
            return len(lst)

        return int(self._locked_update(key, _mutate, 0))

    def trim_list(self, key: str, field: str, min_value: Any) -> int:
        """Remove list items less than min_value."""

        def _mutate(bucket: Dict[str, Any]) -> int:
            lst = bucket.get(field, [])
            if not isinstance(lst, list):
                return 0
            original_len = len(lst)
            lst = [v for v in lst if v >= min_value]
            bucket[field] = lst
            return original_len - len(lst)

        return int(self._locked_update(key, _mutate, 0))

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

    @staticmethod
    def _lock_failure_outcome(key: str, reason: str) -> tuple:
        """Fail closed when the token-bucket lock cannot be acquired.

        The Lua spend is atomic on its own, but it also reads/writes the same
        bucket that ``update_bucket`` (secondary hourly/daily quotas) mutates
        under ``extlock:<key>``. Without taking that SAME lock here the two
        operations interleave: a locked secondary-limit write can be clobbered
        by an unnoticed Lua spend (and vice versa). Treating an unacquirable
        lock as "allowed" would reopen the cross-worker double-spend window,
        so deny and make the condition loud.
        """
        from utils import logger as _log

        _log.critical(
            "rate-limit Valkey token bucket could not acquire distributed "
            "lock for %r (%s); failing closed",
            key,
            reason,
        )
        return False, 0, 60.0

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
        lock_key = f"extlock:{key}"
        # Serialise the Lua spend with every other bucket mutation (notably
        # the secondary-limit update_bucket that follows on the same key).
        # Locking fails closed: never fall through to an unlocked spend.
        try:
            token = client.acquire_lock(lock_key, timeout=5.0)
        except Exception as exc:
            return self._lock_failure_outcome(key, str(exc))
        if not token:
            return self._lock_failure_outcome(key, "timeout")

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
        except Exception as exc:
            return self._backend_error_outcome(exc, capacity)
        finally:
            try:
                client.release_lock(lock_key, token)
            except Exception as release_error:
                import utils.logger as logger

                logger.debug(
                    f"rate limit token-bucket lock release failed: {release_error}"
                )
        # Prune after the lock is released (see _locked_update): the O(N) scan
        # must never extend the critical section past the distributed lock's TTL.
        self._maybe_enforce_key_cap(client)
        return allowed, remaining, reset_after

    def _fused_backend_error_outcome(self, exc: Exception, capacity: int) -> tuple:
        """Four-tuple form of :meth:`_backend_error_outcome`.

        ``applied`` mirrors ``allowed`` so an explicitly configured fail-open
        deployment still allows the request, while the default fail-closed
        mode reports "not applied" and the caller denies it.
        """
        allowed, remaining, reset_after = self._backend_error_outcome(exc, capacity)
        return allowed, remaining, reset_after, allowed

    def eval_token_bucket_with_secondary(
        self,
        key: str,
        capacity: int,
        refill_rate: float,
        cost: int,
        hourly_limit: Optional[int],
        daily_limit: Optional[int],
        unix_now: float,
        effective_limit: int,
        ttl: int = 86400,
        apply_secondary: Optional[Callable[[Dict[str, Any]], Optional[float]]] = None,
    ) -> tuple:
        """Spend a token and apply hourly/daily quotas in ONE locked script.

        ``apply_secondary`` is accepted for interface parity with the base
        fallback; the fused Lua script performs the quota maths server-side.

        Returns ``(allowed, remaining, reset_after, applied)``. ``applied`` is
        False only when the store could not verify the mutation (lock timeout
        or backend error); callers must fail closed in that case.
        """
        client = get_client()
        if not client or not is_available():
            return False, 0, 60.0, False

        full_key = self._get_key(key)
        lock_key = f"extlock:{key}"
        try:
            token = client.acquire_lock(lock_key, timeout=5.0)
        except Exception as exc:
            return (*self._lock_failure_outcome(key, str(exc)), False)
        if not token:
            return (*self._lock_failure_outcome(key, "timeout"), False)

        try:
            result = client.eval_lua(
                _TOKEN_BUCKET_WITH_SECONDARY_LUA,
                keys=[full_key],
                args=[
                    capacity,
                    refill_rate,
                    cost,
                    ttl,
                    unix_now,
                    hourly_limit or 0,
                    daily_limit or 0,
                    effective_limit,
                ],
            )
            allowed = _script_truth(result[0])
            remaining = int(float(_decode_script_scalar(result[1])))
            reset_after = float(_decode_script_scalar(result[2]))
            applied = _script_truth(result[3])
        except Exception as exc:
            return self._fused_backend_error_outcome(exc, capacity)
        finally:
            try:
                client.release_lock(lock_key, token)
            except Exception as release_error:
                import utils.logger as logger

                logger.debug(
                    f"rate limit token-bucket lock release failed: {release_error}"
                )
        # Prune after the lock is released (see _locked_update).
        self._maybe_enforce_key_cap(client)
        return allowed, remaining, reset_after, applied

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
        Returns ``default`` when the backend is unavailable or errors, but
        FAILS CLOSED (raises) when the distributed lock cannot be acquired --
        an unlocked read-modify-write silently drops the counter deduction.
        """
        client = get_client()
        if not client or not is_available():
            return default

        full_key = self._get_key(key)
        token = await client.aacquire_lock(
            f"extlock:{key}", timeout=self._MUTATOR_LOCK_TIMEOUT
        )
        if not token:
            raise RateLimitStoreUnavailable(
                f"could not acquire distributed rate-limit lock for {key!r}"
            )
        try:
            bucket = cast(Dict[str, Any], await client.aget_json(full_key) or {})
            result = mutate(bucket)
            await client.aset_json(full_key, bucket, ttl=self.DEFAULT_BUCKET_TTL)
        except ValkeyError:
            return default
        finally:
            await client.arelease_lock(f"extlock:{key}", token)
        # Prune after the lock is released (see _locked_update).
        await self._amaybe_enforce_key_cap(client)
        return result

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
            await self._amaybe_enforce_key_cap(client)
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
        lock_key = f"extlock:{key}"
        # Same lock namespace as aupdate_bucket / the mutator paths. See the
        # sync eval_token_bucket docstring for why this cannot be skipped.
        try:
            token = await client.aacquire_lock(lock_key, timeout=5.0)
        except Exception as exc:
            return self._lock_failure_outcome(key, str(exc))
        if not token:
            return self._lock_failure_outcome(key, "timeout")

        try:
            now = time.time()
            result = await client.aeval_lua(
                script, keys=[full_key], args=[capacity, refill_rate, cost, ttl, now]
            )
            allowed = _script_truth(result[0])
            remaining = int(float(_decode_script_scalar(result[1])))
            reset_after = float(_decode_script_scalar(result[2]))
        except Exception as exc:
            return self._backend_error_outcome(exc, capacity)
        finally:
            try:
                await client.arelease_lock(lock_key, token)
            except Exception as release_error:
                import utils.logger as logger

                logger.debug(
                    f"rate limit token-bucket lock release failed: {release_error}"
                )
        # Prune after the lock is released (see _locked_update).
        await self._amaybe_enforce_key_cap(client)
        return allowed, remaining, reset_after

    async def aeval_token_bucket_with_secondary(
        self,
        key: str,
        capacity: int,
        refill_rate: float,
        cost: int,
        hourly_limit: Optional[int],
        daily_limit: Optional[int],
        unix_now: float,
        effective_limit: int,
        ttl: int = 86400,
        apply_secondary: Optional[Callable[[Dict[str, Any]], Optional[float]]] = None,
    ) -> tuple:
        """Awaitable counterpart of :meth:`eval_token_bucket_with_secondary`."""
        client = get_client()
        if not client or not is_available():
            return False, 0, 60.0, False

        full_key = self._get_key(key)
        lock_key = f"extlock:{key}"
        try:
            token = await client.aacquire_lock(lock_key, timeout=5.0)
        except Exception as exc:
            return (*self._lock_failure_outcome(key, str(exc)), False)
        if not token:
            return (*self._lock_failure_outcome(key, "timeout"), False)

        try:
            result = await client.aeval_lua(
                _TOKEN_BUCKET_WITH_SECONDARY_LUA,
                keys=[full_key],
                args=[
                    capacity,
                    refill_rate,
                    cost,
                    ttl,
                    unix_now,
                    hourly_limit or 0,
                    daily_limit or 0,
                    effective_limit,
                ],
            )
            allowed = _script_truth(result[0])
            remaining = int(float(_decode_script_scalar(result[1])))
            reset_after = float(_decode_script_scalar(result[2]))
            applied = _script_truth(result[3])
        except Exception as exc:
            return self._fused_backend_error_outcome(exc, capacity)
        finally:
            try:
                await client.arelease_lock(lock_key, token)
            except Exception as release_error:
                import utils.logger as logger

                logger.debug(
                    f"rate limit token-bucket lock release failed: {release_error}"
                )
        # Prune after the lock is released (see _locked_update).
        await self._amaybe_enforce_key_cap(client)
        return allowed, remaining, reset_after, applied

    async def aupdate_bucket(
        self,
        key: str,
        mutate_fn: Callable[[Dict[str, Any]], None],
        ttl: Optional[float] = None,
    ) -> None:
        client = get_client()
        if not client or not is_available():
            # SECURITY: this is a read-modify-write that carries abuse budgets
            # (custom usage counters: upload bytes, sticker/friend-request
            # quotas, ...). Silently returning here made every caller that
            # relies on the returned counter see the caller-supplied ``amount``
            # instead of the authoritative stored value, so a Valkey outage
            # disabled those rate limits entirely (fail-open). The blocking
            # path goes through ``RateLimitStorage.update_bucket``, which
            # raises ``RateLimitStoreUnavailable`` when its lock cannot be
            # acquired; do the same here so the two paths agree and callers
            # can fail closed.
            raise RateLimitStoreUnavailable(
                f"rate-limit store unavailable for bucket {key!r}"
            )

        full_key = self._get_key(key)
        token = await self.aacquire_lock(key, timeout=5.0)
        # SECURITY: without the distributed lock the get->mutate->set sequence
        # is a lost-update race across workers: two requests can both spend
        # the same token. Never fall through to an unlocked mutation.
        if not token:
            raise RateLimitStoreUnavailable(
                f"could not acquire distributed rate-limit lock for {key!r}"
            )
        try:
            state = cast(Dict[str, Any], await client.aget_json(full_key) or {})
            mutate_fn(state)
            int_ttl = int(ttl) if ttl is not None else self.DEFAULT_BUCKET_TTL
            await client.aset_json(full_key, state, ttl=int_ttl)
        except ValkeyError as exc:
            raise RateLimitStoreUnavailable(
                f"rate-limit bucket write failed for {key!r}: {exc}"
            ) from exc
        finally:
            await self.arelease_lock(key, token)
        # Prune after the lock is released (see _locked_update).
        await self._amaybe_enforce_key_cap(client)

    async def aclose(self) -> None:
        """Valkey bucket state is server-side; nothing to close locally."""
        return None
