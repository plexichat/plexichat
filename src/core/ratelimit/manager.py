"""
Rate limit manager - Core rate limiting logic.
"""

import hashlib
import time
import re
from typing import Optional, Dict, Any, Callable, List

from .models import (
    RateLimitBucket,
    RateLimitConfig,
    RateLimitResult,
    RateLimitHeaders,
    BucketType,
    RateLimitAlgorithm,
)
from .config import (
    DEFAULT_ROUTE_LIMITS,
    DEFAULT_WEBHOOK_CHANNEL_LIMIT,
    get_default_config,
    is_bot_higher_limit_route,
    merge_route_configs,
    get_global_limit,
    get_user_limit,
    get_ip_limit,
    should_bypass_admin,
    should_bypass_internal,
)
from .storage import MemoryStorage, RateLimitStorage


def _admin_bypass_enabled() -> bool:
    """Config-gated admin bypass (defaults to enabled)."""
    try:
        return bool(should_bypass_admin())
    except Exception:
        return True


def _internal_bypass_enabled() -> bool:
    """Config-gated internal-request bypass (defaults to enabled)."""
    try:
        return bool(should_bypass_internal())
    except Exception:
        return True


class RateLimitManager:
    """Manages rate limit checking and bucket state with atomic operations."""

    # F8: bound the route component of bucket keys. The middleware already
    # normalizes paths, but defence-in-depth caps the key here too so a
    # 5KB route string can never become a 5KB storage key.
    MAX_ROUTE_KEY_LENGTH = 256

    def __init__(
        self,
        storage_backend: Optional[RateLimitStorage] = None,
        route_configs: Optional[Dict[str, RateLimitConfig]] = None,
        global_config: Optional[RateLimitConfig] = None,
        user_config: Optional[RateLimitConfig] = None,
        ip_config: Optional[RateLimitConfig] = None,
        bot_multiplier: float = 1.2,
        webhook_multiplier: float = 1.0,
        bypass_check: Optional[Callable] = None,
        enable_global_limit: bool = True,
    ):
        """Initialize rate limit manager."""
        self._storage = storage_backend or MemoryStorage()
        self._route_configs = merge_route_configs(
            DEFAULT_ROUTE_LIMITS, route_configs or {}
        )
        self._global_config = global_config or get_global_limit()
        self._user_config = user_config or get_user_limit()
        self._ip_config = ip_config or get_ip_limit()
        self._bot_multiplier = bot_multiplier
        self._webhook_multiplier = webhook_multiplier
        self._bypass_check = bypass_check
        self._enable_global_limit = enable_global_limit

        # Pre-compiled route patterns for performance
        self._compiled_routes = {
            route: self._compile_route(route) for route in self._route_configs.keys()
        }

    def _compile_route(self, route: str) -> re.Pattern:
        """Compile a route pattern like 'GET /channels/{id}/messages' to a regex."""
        # Escape special characters but keep our placeholders
        pattern = re.escape(route)
        pattern = pattern.replace(r"\{id\}", r"\d+")
        pattern = pattern.replace(r"\{msg_id\}", r"\d+")
        pattern = pattern.replace(r"\{emoji\}", r"[^/]+")
        return re.compile(f"^{pattern}$")

    def set_bypass_check(self, bypass_check: Callable) -> None:
        """Set the bypass check function."""
        self._bypass_check = bypass_check

    def _generate_bucket_key(
        self,
        bucket_type: BucketType,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        route: Optional[str] = None,
        resource_id: Optional[int] = None,
        webhook_id: Optional[int] = None,
    ) -> str:
        """Generate a unique bucket key."""
        parts = [bucket_type.value]
        if user_id is not None:
            parts.append(f"u:{user_id}")
        elif ip_address is not None:
            parts.append(f"ip:{ip_address}")

        if route:
            # F8: cap the route component so unbounded path input cannot
            # create unbounded storage keys. Long routes are truncated and
            # disambiguated with a hash suffix (still deterministic, so
            # per-route limiting keeps working).
            if len(route) > self.MAX_ROUTE_KEY_LENGTH:
                digest = hashlib.sha256(route.encode("utf-8")).hexdigest()[:16]
                budget = max(32, self.MAX_ROUTE_KEY_LENGTH - 17)
                route = f"{route[:budget]}#{digest}"
            parts.append(f"r:{route}")
        if resource_id is not None:
            parts.append(f"res:{resource_id}")
        if webhook_id is not None:
            parts.append(f"wh:{webhook_id}")
        key = ":".join(parts)
        # F8: belt-and-braces total key cap (prefixes + ids included).
        if len(key) > self.MAX_ROUTE_KEY_LENGTH + 32:
            digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
            key = f"{key[: self.MAX_ROUTE_KEY_LENGTH]}#{digest}"
        return key

    def _generate_bucket_id(self, key: str) -> str:
        """Generate a short bucket ID for headers."""
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _build_result(
        self,
        key: str,
        bucket_type: BucketType,
        rl_config: RateLimitConfig,
        allowed: bool,
        remaining: int,
        reset_after: float,
        unix_now: float,
        cost: int,
    ) -> RateLimitResult:
        """Assemble a :class:`RateLimitResult` from a raw bucket outcome.

        Shared by the blocking and awaitable evaluation paths so the two
        can never report different headers for the same bucket state.
        """
        return RateLimitResult(
            allowed=allowed,
            headers=RateLimitHeaders(
                limit=rl_config.effective_limit,
                remaining=remaining,
                reset=unix_now + reset_after,
                reset_after=reset_after,
                bucket=self._generate_bucket_id(key),
                is_global=(bucket_type == BucketType.GLOBAL),
                retry_after=reset_after if not allowed else None,
                scope=bucket_type.value,
            ),
            bucket_key=key,
            bucket_type=bucket_type,
            remaining=remaining,
            reset_at=unix_now + reset_after,
            retry_after=reset_after if not allowed else None,
            is_global=(bucket_type == BucketType.GLOBAL),
            limited_by=bucket_type.value if not allowed else None,
            cost=cost,
        )

    def _apply_secondary_limits(
        self,
        state: Dict[str, Any],
        rl_config: RateLimitConfig,
        cost: int,
        unix_now: float,
        outcome: Dict[str, Any],
    ) -> None:
        """Apply the hourly/daily limits to a bucket under the backend lock.

        Shared by the blocking and awaitable token-bucket paths. When a
        secondary limit denies the request we ROLL BACK the already-consumed
        primary token: near-cap traffic must not burn primary quota without
        any successful request.
        """
        secondary_reset_after: Optional[float] = None

        if rl_config.hourly_limit:
            h_allowed, _, h_reset = self._check_hourly_limit(
                state, rl_config, cost, unix_now
            )
            if not h_allowed:
                secondary_reset_after = h_reset

        if secondary_reset_after is None and rl_config.daily_limit:
            d_allowed, _, d_reset = self._check_daily_limit(
                state, rl_config, cost, unix_now
            )
            if not d_allowed:
                secondary_reset_after = d_reset

        if secondary_reset_after is not None:
            # Roll back the consumed primary token (best effort, capped at
            # the bucket capacity).
            tokens = float(state.get("tokens", 0.0))
            state["tokens"] = min(float(rl_config.effective_limit), tokens + cost)

        outcome["denied"] = secondary_reset_after

    def _apply_legacy_check(
        self,
        state: Dict[str, Any],
        rl_config: RateLimitConfig,
        cost: int,
        unix_now: float,
        outcome: Dict[str, Any],
    ) -> None:
        """Apply a non-token-bucket algorithm plus secondary limits.

        Shared by the blocking and awaitable legacy paths.
        """
        if rl_config.algorithm == RateLimitAlgorithm.FIXED_WINDOW:
            l_allowed, l_remaining, l_reset = self._legacy_fixed_window(
                state, rl_config, cost, unix_now
            )
        elif rl_config.algorithm == RateLimitAlgorithm.LEAKY_BUCKET:
            l_allowed, l_remaining, l_reset = self._legacy_leaky_bucket(
                state, rl_config, cost, unix_now
            )
        else:
            # Default to sliding window logic if not specified
            l_allowed, l_remaining, l_reset = self._legacy_sliding_window(
                state, rl_config, cost, unix_now
            )

        # Check secondary limits for legacy path too
        if l_allowed and (rl_config.hourly_limit or rl_config.daily_limit):
            if rl_config.hourly_limit:
                h_allowed, _, h_reset = self._check_hourly_limit(
                    state, rl_config, cost, unix_now
                )
                if not h_allowed:
                    l_allowed = False
                    l_reset = h_reset
            if l_allowed and rl_config.daily_limit:
                d_allowed, _, d_reset = self._check_daily_limit(
                    state, rl_config, cost, unix_now
                )
                if not d_allowed:
                    l_allowed = False
                    l_reset = d_reset

        outcome.update(allowed=l_allowed, remaining=l_remaining, reset_after=l_reset)

    def _get_config_for_request(
        self,
        route: Optional[str],
        is_bot: bool,
        is_webhook: bool,
        override_config: Optional[RateLimitConfig] = None,
    ) -> RateLimitConfig:
        """Get the appropriate config for a request.

        ``override_config`` lets a caller (e.g. the ``@rate_limit``
        decorator) supply its limit directly instead of relying on the shared
        ``_route_configs`` registry, which a concurrent request can mutate
        between the caller's await points.
        """
        if override_config is not None:
            config = override_config
        else:
            config = self._route_configs.get(route or "")
            if config is None:
                config = get_default_config()

        if is_bot and is_bot_higher_limit_route(route or ""):
            config = config.with_multiplier(self._bot_multiplier)
        elif is_webhook:
            config = config.with_multiplier(self._webhook_multiplier)
        return config

    def _check_bypass(
        self,
        user_id: Optional[int],
        is_admin: bool,
        is_internal: bool,
    ) -> bool:
        """Check if request should bypass rate limiting.

        SECURITY: bypass behaviour is config-gated. ``rate_limiting
        .admin_bypass`` (via :func:`should_bypass_admin`) and
        ``rate_limiting.internal_bypass`` (via
        :func:`should_bypass_internal`) must both be honoured — a
        hardcoded unconditional admin exemption meant setting the flag
        to false had no runtime effect. A custom ``bypass_check``
        still takes precedence over the default admin decision.
        """
        if is_internal and _internal_bypass_enabled():
            return True
        if self._bypass_check:
            return self._bypass_check(user_id, is_admin, is_internal)
        if is_admin and _admin_bypass_enabled():
            return True
        return False

    def _check_atomic(
        self,
        key: str,
        bucket_type: BucketType,
        rl_config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Evaluate a bucket using atomic storage operations if possible."""
        # Use atomic eval if storage supports it and algorithm is token bucket
        # We also need to check hourly/daily limits if they are set
        if rl_config.algorithm == RateLimitAlgorithm.TOKEN_BUCKET:
            refill_rate = rl_config.requests / rl_config.window_seconds
            allowed, remaining, reset_after = self._storage.eval_token_bucket(
                key, rl_config.effective_limit, refill_rate, cost
            )

            # If primary allowed, check secondary limits (hourly/daily).
            # SECURITY: the read-modify-write of the hourly/daily counters
            # runs atomically on the storage backend (per-key lock held
            # across get->mutate->set) so concurrent requests on the same
            # bucket cannot lose counter updates. When a secondary limit
            # denies the request we ROLL BACK the already-consumed primary
            # token: near-cap traffic must not burn primary quota without
            # any successful request.
            if allowed and (rl_config.hourly_limit or rl_config.daily_limit):
                outcome: Dict[str, Any] = {}

                self._storage.update_bucket(
                    key,
                    lambda state: self._apply_secondary_limits(
                        state, rl_config, cost, unix_now, outcome
                    ),
                    ttl=86400,
                )

                denied_reset = outcome.get("denied")
                if denied_reset is not None:
                    allowed = False
                    reset_after = denied_reset

            return self._build_result(
                key,
                bucket_type,
                rl_config,
                allowed,
                remaining,
                reset_after,
                unix_now,
                cost,
            )

        # Legacy check for non-token-bucket algorithms.
        # SECURITY: the get_bucket()->mutate->set_bucket sequence is a
        # read-modify-write that MUST be atomic. Previously two concurrent
        # requests could both read the same state and the second write
        # clobbered the first, effectively doubling (or worse) the
        # configured limit under concurrency. ``update_bucket`` holds the
        # per-key storage lock across the whole check+update.
        outcome: Dict[str, Any] = {}

        self._storage.update_bucket(
            key,
            lambda state: self._apply_legacy_check(
                state, rl_config, cost, unix_now, outcome
            ),
            ttl=max(rl_config.window_seconds * 2, 86400),
        )

        allowed = outcome["allowed"]
        remaining = outcome["remaining"]
        reset_after = outcome["reset_after"]

        return self._build_result(
            key,
            bucket_type,
            rl_config,
            allowed,
            remaining,
            reset_after,
            unix_now,
            cost,
        )

    def _legacy_fixed_window(self, state, config, cost, unix_now):
        window_start = state.get("window_start", unix_now)
        request_count = state.get("request_count", 0)

        if unix_now >= window_start + config.window_seconds:
            window_start = unix_now
            request_count = 0

        if request_count + cost <= config.effective_limit:
            request_count += cost
            allowed = True
        else:
            allowed = False

        state["window_start"] = window_start
        state["request_count"] = request_count
        remaining = max(0, config.effective_limit - request_count)
        reset_after = (window_start + config.window_seconds) - unix_now
        return allowed, remaining, reset_after

    def _legacy_sliding_window(self, state, config, cost, unix_now):
        cutoff = unix_now - config.window_seconds
        timestamps = state.get("timestamps", [])
        timestamps = [ts for ts in timestamps if ts > cutoff]

        if len(timestamps) + cost <= config.effective_limit:
            for _ in range(cost):
                timestamps.append(unix_now)
            allowed = True
        else:
            allowed = False

        state["timestamps"] = timestamps
        remaining = max(0, config.effective_limit - len(timestamps))
        reset_after = timestamps[0] - cutoff if timestamps else config.window_seconds
        return allowed, remaining, reset_after

    def _legacy_leaky_bucket(self, state, config, cost, unix_now):
        water_level = state.get("water_level", 0.0)
        last_leak = state.get("last_leak", unix_now)
        leak_rate = config.requests / config.window_seconds
        elapsed = unix_now - last_leak

        water_level = max(0.0, water_level - (elapsed * leak_rate))

        if water_level + cost <= config.effective_limit:
            water_level += cost
            allowed = True
        else:
            allowed = False

        state["water_level"] = water_level
        state["last_leak"] = unix_now
        remaining = max(0, int(config.effective_limit - water_level))
        reset_after = (
            (water_level + cost - config.effective_limit) / leak_rate
            if not allowed
            else 0.0
        )
        return allowed, remaining, reset_after

    def _check_hourly_limit(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> tuple:
        """Check hourly limit."""
        if not config.hourly_limit:
            return True, 0, 0.0
        hourly_reset = state.get("hourly_reset", unix_now + 3600)
        hourly_count = state.get("hourly_count", 0)
        if unix_now >= hourly_reset:
            hourly_reset = unix_now + 3600
            hourly_count = 0
        if hourly_count + cost <= config.hourly_limit:
            hourly_count += cost
            allowed = True
        else:
            allowed = False
        state["hourly_reset"] = hourly_reset
        state["hourly_count"] = hourly_count
        remaining = max(0, config.hourly_limit - hourly_count)
        reset_after = hourly_reset - unix_now
        return allowed, remaining, reset_after

    def _check_daily_limit(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> tuple:
        """Check daily limit."""
        if not config.daily_limit:
            return True, 0, 0.0
        daily_reset = state.get("daily_reset", unix_now + 86400)
        daily_count = state.get("daily_count", 0)
        if unix_now >= daily_reset:
            daily_reset = unix_now + 86400
            daily_count = 0
        if daily_count + cost <= config.daily_limit:
            daily_count += cost
            allowed = True
        else:
            allowed = False
        state["daily_reset"] = daily_reset
        state["daily_count"] = daily_count
        remaining = max(0, config.daily_limit - daily_count)
        reset_after = daily_reset - unix_now
        return allowed, remaining, reset_after

    def _bucket_from_state(
        self, state: Dict[str, Any], bucket_key: str
    ) -> RateLimitBucket:
        """Build a :class:`RateLimitBucket` from a raw state dict.

        Shared by the blocking and awaitable ``get_bucket_info`` paths.
        """
        return RateLimitBucket(
            key=state.get("key", bucket_key),
            bucket_type=BucketType(state.get("bucket_type", "route")),
            config=get_default_config(),
            tokens=state.get("tokens", 0.0),
            last_update=state.get("last_update", 0.0),
            window_start=state.get("window_start", 0.0),
            request_count=state.get("request_count", 0),
            hourly_count=state.get("hourly_count", 0),
            hourly_reset=state.get("hourly_reset", 0.0),
            daily_count=state.get("daily_count", 0),
            daily_reset=state.get("daily_reset", 0.0),
            request_timestamps=state.get("timestamps", []),
        )

    def get_bucket_info(self, bucket_key: str) -> Optional[RateLimitBucket]:
        """Get bucket information."""
        state = self._storage.get_bucket(bucket_key)
        if state is None:
            return None
        return self._bucket_from_state(state, bucket_key)

    def check_rate_limit(
        self,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        route: Optional[str] = None,
        resource_id: Optional[int] = None,
        is_bot: bool = False,
        is_webhook: bool = False,
        is_admin: bool = False,
        is_internal: bool = False,
        webhook_id: Optional[int] = None,
        cost: int = 1,
    ) -> RateLimitResult:
        """
        Check if a request is rate limited.
        Implements a transactional-style check to prevent token leakage.
        """
        unix_now = time.time()
        if self._check_bypass(user_id, is_admin, is_internal):
            return self._create_bypass_result(route, unix_now)

        checks: List[RateLimitResult] = []

        # 1. Global limit (User OR IP)
        if self._enable_global_limit:
            if user_id is not None:
                checks.append(self._check_global_limit(user_id, cost, unix_now))
            elif ip_address is not None:
                key = self._generate_bucket_key(
                    BucketType.GLOBAL, ip_address=ip_address
                )
                checks.append(
                    self._check_atomic(
                        key, BucketType.GLOBAL, self._global_config, cost, unix_now
                    )
                )

        # 2. User or IP limit
        if user_id is not None and not is_webhook:
            checks.append(self._check_user_limit(user_id, cost, unix_now))
        elif ip_address is not None and not is_webhook:
            checks.append(self._check_ip_limit(ip_address, cost, unix_now))

        # 3. Route/Resource/Webhook limit
        if route:
            config = self._get_config_for_request(route, is_bot, is_webhook)
            if config.scope == BucketType.RESOURCE and resource_id is not None:
                checks.append(
                    self._check_resource_limit(
                        user_id, route, resource_id, config, cost, unix_now
                    )
                )
            elif config.scope == BucketType.WEBHOOK and webhook_id is not None:
                checks.append(
                    self._check_webhook_limit(
                        webhook_id, route, resource_id, config, cost, unix_now
                    )
                )
            else:
                checks.append(
                    self._check_route_limit(
                        user_id, ip_address, route, config, cost, unix_now
                    )
                )

        # Perform all checks
        for result in checks:
            if not result.allowed:
                return result

        # Return the most specific successful result (usually the route/resource limit)
        # to provide the most useful headers to the client.
        return checks[-1] if checks else self._create_bypass_result(route, unix_now)

    def increment_custom_usage(
        self, key: str, field: str, amount: int, ttl: int = 86400
    ) -> int:
        """
        Increment a custom usage counter directly in storage.
        Useful for non-request metrics like daily upload size.
        """
        outcome: Dict[str, int] = {}

        # Keep the counter and its expiry under the same backend lock. This
        # matters for shared report/abuse counters: a read-then-increment
        # sequence permits concurrent requests to lose updates, and the old
        # implementation ignored ``ttl`` entirely.
        self._storage.update_bucket(
            key,
            lambda state: self._apply_custom_increment(state, field, amount, outcome),
            ttl=ttl,
        )
        return outcome.get("value", amount)

    def _apply_custom_increment(
        self,
        state: Dict[str, Any],
        field: str,
        amount: int,
        outcome: Dict[str, int],
    ) -> None:
        """Add ``amount`` to ``state[field]`` and record the new value.

        Shared by the blocking and awaitable custom-counter paths.
        """
        current = state.get(field, 0)
        try:
            current = int(current)
        except (TypeError, ValueError):
            current = 0
        new_value = current + amount
        state[field] = new_value
        outcome["value"] = new_value

    def _check_global_limit(
        self,
        user_id: int,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check global rate limit."""
        key = self._generate_bucket_key(BucketType.GLOBAL, user_id=user_id)
        return self._check_atomic(
            key, BucketType.GLOBAL, self._global_config, cost, unix_now
        )

    def _check_user_limit(
        self,
        user_id: int,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check per-user rate limit."""
        key = self._generate_bucket_key(BucketType.USER, user_id=user_id)
        return self._check_atomic(
            key, BucketType.USER, self._user_config, cost, unix_now
        )

    def _check_ip_limit(
        self,
        ip_address: str,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check per-IP rate limit (for unauthenticated users)."""
        key = self._generate_bucket_key(BucketType.IP, ip_address=ip_address)
        return self._check_atomic(key, BucketType.IP, self._ip_config, cost, unix_now)

    def _check_route_limit(
        self,
        user_id: Optional[int],
        ip_address: Optional[str],
        route: str,
        config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check per-route rate limit."""
        key = self._generate_bucket_key(
            BucketType.ROUTE, user_id=user_id, ip_address=ip_address, route=route
        )
        return self._check_atomic(key, BucketType.ROUTE, config, cost, unix_now)

    def _check_resource_limit(
        self,
        user_id: Optional[int],
        route: str,
        resource_id: int,
        config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check per-resource rate limit."""
        key = self._generate_bucket_key(
            BucketType.RESOURCE,
            user_id=user_id,
            route=route,
            resource_id=resource_id,
        )
        return self._check_atomic(key, BucketType.RESOURCE, config, cost, unix_now)

    def _check_webhook_limit(
        self,
        webhook_id: int,
        route: str,
        resource_id: Optional[int],
        config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check webhook rate limit."""
        key = self._generate_bucket_key(
            BucketType.WEBHOOK,
            webhook_id=webhook_id,
            route=route,
        )

        # 1. Check the specific webhook's limit
        result = self._check_atomic(key, BucketType.WEBHOOK, config, cost, unix_now)
        if not result.allowed:
            return result

        # 2. Check the channel-wide webhook limit (if resource_id provided)
        if resource_id is not None:
            channel_key = self._generate_bucket_key(
                BucketType.CHANNEL_WEBHOOK,
                resource_id=resource_id,
            )
            channel_result = self._check_atomic(
                channel_key,
                BucketType.CHANNEL_WEBHOOK,
                DEFAULT_WEBHOOK_CHANNEL_LIMIT,
                cost,
                unix_now,
            )
            if not channel_result.allowed:
                return channel_result

        return result

    def _create_bypass_result(
        self, route: Optional[str], unix_now: float
    ) -> RateLimitResult:
        """Create a result for bypassed requests."""
        return RateLimitResult(
            allowed=True,
            headers=RateLimitHeaders(
                limit=999999,
                remaining=999999,
                reset=unix_now + 60,
                reset_after=60.0,
                bucket="bypass",
                is_global=False,
                scope="bypass",
            ),
            bucket_key="bypass",
            bucket_type=BucketType.USER,
            remaining=999999,
            reset_at=unix_now + 60,
            cost=0,
        )

    def get_headers(self, result: RateLimitResult) -> Dict[str, str]:
        """Get HTTP headers from a rate limit result."""
        return result.headers.to_dict()

    def reset_bucket(self, bucket_key: str) -> None:
        """Reset a specific bucket."""
        self._storage.delete_bucket(bucket_key)

    def reset_user(self, user_id: int) -> None:
        """Reset all buckets for a user.

        SECURITY: matching must be an EXACT segment match on the
        ``u:{id}`` key component. The previous ``prefix in key``
        substring check deleted buckets of unrelated users (user 1
        also matched users 10..19, 100..199, and any resource-scoped
        key merely containing ``u:1``).
        """
        pattern = re.compile(rf"(?:^|:)u:{re.escape(str(user_id))}(?=$|:)")
        keys = self._storage.get_keys_by_prefix("")
        for key in keys:
            if pattern.search(key):
                self._storage.delete_bucket(key)

    def reset_all(self) -> None:
        """Reset all buckets."""
        self._storage.clear_all()

    # ------------------------------------------------------------------
    # Async API
    # ------------------------------------------------------------------
    # Every blocking method above has an awaitable counterpart below. The
    # pure computation -- algorithms, key generation, secondary limits,
    # result assembly -- is shared through ``_apply_*``/``_build_result``,
    # so the two paths cannot diverge; only the storage round trips differ.

    async def acheck_rate_limit(
        self,
        user_id: Optional[int] = None,
        ip_address: Optional[str] = None,
        route: Optional[str] = None,
        resource_id: Optional[int] = None,
        is_bot: bool = False,
        is_webhook: bool = False,
        is_admin: bool = False,
        is_internal: bool = False,
        webhook_id: Optional[int] = None,
        cost: int = 1,
        override_config: Optional[RateLimitConfig] = None,
    ) -> RateLimitResult:
        """Awaitable counterpart of :meth:`check_rate_limit`.

        Runs the same global -> user/IP -> route/resource/webhook sequence
        without blocking the event loop on the storage backend.
        """
        unix_now = time.time()
        if self._check_bypass(user_id, is_admin, is_internal):
            return self._create_bypass_result(route, unix_now)

        checks: List[RateLimitResult] = []

        # 1. Global limit (User OR IP)
        if self._enable_global_limit:
            if user_id is not None:
                checks.append(await self._acheck_global_limit(user_id, cost, unix_now))
            elif ip_address is not None:
                key = self._generate_bucket_key(
                    BucketType.GLOBAL, ip_address=ip_address
                )
                checks.append(
                    await self._acheck_atomic(
                        key, BucketType.GLOBAL, self._global_config, cost, unix_now
                    )
                )

        # 2. User or IP limit
        if user_id is not None and not is_webhook:
            checks.append(await self._acheck_user_limit(user_id, cost, unix_now))
        elif ip_address is not None and not is_webhook:
            checks.append(await self._acheck_ip_limit(ip_address, cost, unix_now))

        # 3. Route/Resource/Webhook limit
        if route:
            config = self._get_config_for_request(
                route, is_bot, is_webhook, override_config
            )
            if config.scope == BucketType.RESOURCE and resource_id is not None:
                checks.append(
                    await self._acheck_resource_limit(
                        user_id, route, resource_id, config, cost, unix_now
                    )
                )
            elif config.scope == BucketType.WEBHOOK and webhook_id is not None:
                checks.append(
                    await self._acheck_webhook_limit(
                        webhook_id, route, resource_id, config, cost, unix_now
                    )
                )
            else:
                checks.append(
                    await self._acheck_route_limit(
                        user_id, ip_address, route, config, cost, unix_now
                    )
                )

        for result in checks:
            if not result.allowed:
                return result

        return checks[-1] if checks else self._create_bypass_result(route, unix_now)

    async def _acheck_atomic(
        self,
        key: str,
        bucket_type: BucketType,
        rl_config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Awaitable counterpart of :meth:`_check_atomic`."""
        if rl_config.algorithm == RateLimitAlgorithm.TOKEN_BUCKET:
            refill_rate = rl_config.requests / rl_config.window_seconds
            allowed, remaining, reset_after = await self._storage.aeval_token_bucket(
                key, rl_config.effective_limit, refill_rate, cost
            )

            if allowed and (rl_config.hourly_limit or rl_config.daily_limit):
                outcome: Dict[str, Any] = {}

                await self._storage.aupdate_bucket(
                    key,
                    lambda state: self._apply_secondary_limits(
                        state, rl_config, cost, unix_now, outcome
                    ),
                    ttl=86400,
                )

                denied_reset = outcome.get("denied")
                if denied_reset is not None:
                    allowed = False
                    reset_after = denied_reset

            return self._build_result(
                key,
                bucket_type,
                rl_config,
                allowed,
                remaining,
                reset_after,
                unix_now,
                cost,
            )

        outcome: Dict[str, Any] = {}

        await self._storage.aupdate_bucket(
            key,
            lambda state: self._apply_legacy_check(
                state, rl_config, cost, unix_now, outcome
            ),
            ttl=max(rl_config.window_seconds * 2, 86400),
        )

        allowed = outcome["allowed"]
        remaining = outcome["remaining"]
        reset_after = outcome["reset_after"]

        return self._build_result(
            key,
            bucket_type,
            rl_config,
            allowed,
            remaining,
            reset_after,
            unix_now,
            cost,
        )

    async def _acheck_global_limit(
        self, user_id: int, cost: int, unix_now: float
    ) -> RateLimitResult:
        """Awaitable counterpart of :meth:`_check_global_limit`."""
        key = self._generate_bucket_key(BucketType.GLOBAL, user_id=user_id)
        return await self._acheck_atomic(
            key, BucketType.GLOBAL, self._global_config, cost, unix_now
        )

    async def _acheck_user_limit(
        self, user_id: int, cost: int, unix_now: float
    ) -> RateLimitResult:
        """Awaitable counterpart of :meth:`_check_user_limit`."""
        key = self._generate_bucket_key(BucketType.USER, user_id=user_id)
        return await self._acheck_atomic(
            key, BucketType.USER, self._user_config, cost, unix_now
        )

    async def _acheck_ip_limit(
        self, ip_address: str, cost: int, unix_now: float
    ) -> RateLimitResult:
        """Awaitable counterpart of :meth:`_check_ip_limit`."""
        key = self._generate_bucket_key(BucketType.IP, ip_address=ip_address)
        return await self._acheck_atomic(
            key, BucketType.IP, self._ip_config, cost, unix_now
        )

    async def _acheck_route_limit(
        self,
        user_id: Optional[int],
        ip_address: Optional[str],
        route: str,
        config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Awaitable counterpart of :meth:`_check_route_limit`."""
        key = self._generate_bucket_key(
            BucketType.ROUTE, user_id=user_id, ip_address=ip_address, route=route
        )
        return await self._acheck_atomic(key, BucketType.ROUTE, config, cost, unix_now)

    async def _acheck_resource_limit(
        self,
        user_id: Optional[int],
        route: str,
        resource_id: int,
        config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Awaitable counterpart of :meth:`_check_resource_limit`."""
        key = self._generate_bucket_key(
            BucketType.RESOURCE,
            user_id=user_id,
            route=route,
            resource_id=resource_id,
        )
        return await self._acheck_atomic(
            key, BucketType.RESOURCE, config, cost, unix_now
        )

    async def _acheck_webhook_limit(
        self,
        webhook_id: int,
        route: str,
        resource_id: Optional[int],
        config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Awaitable counterpart of :meth:`_check_webhook_limit`."""
        key = self._generate_bucket_key(
            BucketType.WEBHOOK,
            webhook_id=webhook_id,
            route=route,
        )

        result = await self._acheck_atomic(
            key, BucketType.WEBHOOK, config, cost, unix_now
        )
        if not result.allowed:
            return result

        if resource_id is not None:
            channel_key = self._generate_bucket_key(
                BucketType.CHANNEL_WEBHOOK,
                resource_id=resource_id,
            )
            channel_result = await self._acheck_atomic(
                channel_key,
                BucketType.CHANNEL_WEBHOOK,
                DEFAULT_WEBHOOK_CHANNEL_LIMIT,
                cost,
                unix_now,
            )
            if not channel_result.allowed:
                return channel_result

        return result

    async def aget_bucket_info(self, bucket_key: str) -> Optional[RateLimitBucket]:
        """Awaitable counterpart of :meth:`get_bucket_info`."""
        state = await self._storage.aget_bucket(bucket_key)
        if state is None:
            return None
        return self._bucket_from_state(state, bucket_key)

    async def aincrement_custom_usage(
        self, key: str, field: str, amount: int, ttl: int = 86400
    ) -> int:
        """Awaitable counterpart of :meth:`increment_custom_usage`."""
        outcome: Dict[str, int] = {}

        await self._storage.aupdate_bucket(
            key,
            lambda state: self._apply_custom_increment(state, field, amount, outcome),
            ttl=ttl,
        )
        return outcome.get("value", amount)

    async def areset_bucket(self, bucket_key: str) -> None:
        """Awaitable counterpart of :meth:`reset_bucket`."""
        await self._storage.adelete_bucket(bucket_key)

    async def areset_user(self, user_id: int) -> None:
        """Awaitable counterpart of :meth:`reset_user`.

        Uses the same EXACT-segment ``u:{id}`` match as the blocking form.
        """
        pattern = re.compile(rf"(?:^|:)u:{re.escape(str(user_id))}(?=$|:)")
        keys = await self._storage.aget_keys_by_prefix("")
        for key in keys:
            if pattern.search(key):
                await self._storage.adelete_bucket(key)

    async def areset_all(self) -> None:
        """Awaitable counterpart of :meth:`reset_all`."""
        await self._storage.aclear_all()
