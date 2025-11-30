"""
Rate limit manager - Core rate limiting logic.
"""

import hashlib
import time
from typing import Optional, Dict, Any, Callable

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
    DEFAULT_GLOBAL_LIMIT,
    DEFAULT_USER_LIMIT,
    DEFAULT_WEBHOOK_CHANNEL_LIMIT,
    get_route_config,
    get_default_config,
    is_bot_higher_limit_route,
    merge_route_configs,
)
from .storage import MemoryStorage, RateLimitStorage


class RateLimitManager:
    """Manages rate limit checking and bucket state."""

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
        """
        Initialize rate limit manager.

        Args:
            storage_backend: Storage backend (default: MemoryStorage).
            route_configs: Custom route configurations.
            global_config: Global rate limit config.
            user_config: Per-user rate limit config.
            ip_config: Per-IP rate limit config (for unauthenticated users).
            bot_multiplier: Multiplier for bot limits.
            webhook_multiplier: Multiplier for webhook limits.
            bypass_check: Callable(user_id, is_admin, is_internal) -> bool.
            enable_global_limit: Whether to enforce global limits.
        """
        self._storage = storage_backend or MemoryStorage()
        self._route_configs = merge_route_configs(
            DEFAULT_ROUTE_LIMITS,
            route_configs or {}
        )
        self._global_config = global_config or DEFAULT_GLOBAL_LIMIT
        self._user_config = user_config or DEFAULT_USER_LIMIT
        from .config import DEFAULT_IP_LIMIT
        self._ip_config = ip_config or DEFAULT_IP_LIMIT
        self._bot_multiplier = bot_multiplier
        self._webhook_multiplier = webhook_multiplier
        self._bypass_check = bypass_check
        self._enable_global_limit = enable_global_limit

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
            route_hash = hashlib.md5(route.encode()).hexdigest()[:8]
            parts.append(f"r:{route_hash}")
        if resource_id is not None:
            parts.append(f"res:{resource_id}")
        if webhook_id is not None:
            parts.append(f"wh:{webhook_id}")
        return ":".join(parts)

    def _generate_bucket_id(self, key: str) -> str:
        """Generate a short bucket ID for headers."""
        return hashlib.md5(key.encode()).hexdigest()[:16]

    def _get_config_for_request(
        self,
        route: Optional[str],
        is_bot: bool,
        is_webhook: bool,
    ) -> RateLimitConfig:
        """Get the appropriate config for a request."""
        config = None
        if route:
            config = self._route_configs.get(route)
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
        """Check if request should bypass rate limiting."""
        if is_internal:
            return True
        if self._bypass_check:
            return self._bypass_check(user_id, is_admin, is_internal)
        return is_admin

    def _get_or_create_bucket(
        self,
        key: str,
        bucket_type: BucketType,
        config: RateLimitConfig,
    ) -> Dict[str, Any]:
        """Get existing bucket state or create new one."""
        state = self._storage.get_bucket(key)
        if state is None:
            now = time.monotonic()
            state = {
                "key": key,
                "bucket_type": bucket_type.value,
                "tokens": float(config.effective_limit),
                "last_update": now,
                "window_start": now,
                "request_count": 0,
                "hourly_count": 0,
                "hourly_reset": now + 3600,
                "daily_count": 0,
                "daily_reset": now + 86400,
                "timestamps": [],
            }
            ttl = max(config.window_seconds * 2, 3600)
            self._storage.set_bucket(key, state, ttl=ttl)
        return state

    def _check_token_bucket(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
    ) -> tuple:
        """Check token bucket algorithm. Returns (allowed, remaining, reset_after)."""
        now = time.monotonic()
        tokens = state.get("tokens", float(config.effective_limit))
        last_update = state.get("last_update", now)
        elapsed = now - last_update
        refill_rate = config.requests / config.window_seconds
        tokens = min(config.effective_limit, tokens + elapsed * refill_rate)
        if tokens >= cost:
            tokens -= cost
            allowed = True
        else:
            allowed = False
        state["tokens"] = tokens
        state["last_update"] = now
        remaining = int(tokens)
        if allowed:
            reset_after = 0.0
        else:
            reset_after = (cost - tokens) / refill_rate
        return allowed, remaining, reset_after

    def _check_sliding_window(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
    ) -> tuple:
        """Check sliding window algorithm. Returns (allowed, remaining, reset_after)."""
        now = time.monotonic()
        timestamps = state.get("timestamps", [])
        cutoff = now - config.window_seconds
        timestamps = [ts for ts in timestamps if ts > cutoff]
        current_count = len(timestamps)
        if current_count + cost <= config.effective_limit:
            for _ in range(cost):
                timestamps.append(now)
            allowed = True
        else:
            allowed = False
        state["timestamps"] = timestamps
        remaining = max(0, config.effective_limit - len(timestamps))
        if allowed:
            reset_after = 0.0
        elif timestamps:
            reset_after = timestamps[0] - cutoff
        else:
            reset_after = config.window_seconds
        return allowed, remaining, reset_after

    def _check_fixed_window(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
    ) -> tuple:
        """Check fixed window algorithm. Returns (allowed, remaining, reset_after)."""
        now = time.monotonic()
        window_start = state.get("window_start", now)
        request_count = state.get("request_count", 0)
        if now >= window_start + config.window_seconds:
            window_start = now
            request_count = 0
        if request_count + cost <= config.effective_limit:
            request_count += cost
            allowed = True
        else:
            allowed = False
        state["window_start"] = window_start
        state["request_count"] = request_count
        remaining = max(0, config.effective_limit - request_count)
        reset_after = (window_start + config.window_seconds) - now
        return allowed, remaining, reset_after

    def _check_leaky_bucket(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
    ) -> tuple:
        """Check leaky bucket algorithm. Returns (allowed, remaining, reset_after)."""
        now = time.monotonic()
        water_level = state.get("water_level", 0.0)
        last_leak = state.get("last_leak", now)
        leak_rate = config.requests / config.window_seconds
        elapsed = now - last_leak
        water_level = max(0, water_level - elapsed * leak_rate)
        if water_level + cost <= config.effective_limit:
            water_level += cost
            allowed = True
        else:
            allowed = False
        state["water_level"] = water_level
        state["last_leak"] = now
        remaining = max(0, int(config.effective_limit - water_level))
        if allowed:
            reset_after = 0.0
        else:
            reset_after = (water_level + cost - config.effective_limit) / leak_rate
        return allowed, remaining, reset_after

    def _check_algorithm(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
    ) -> tuple:
        """Check rate limit using configured algorithm."""
        if config.algorithm == RateLimitAlgorithm.TOKEN_BUCKET:
            return self._check_token_bucket(state, config, cost)
        elif config.algorithm == RateLimitAlgorithm.SLIDING_WINDOW:
            return self._check_sliding_window(state, config, cost)
        elif config.algorithm == RateLimitAlgorithm.FIXED_WINDOW:
            return self._check_fixed_window(state, config, cost)
        elif config.algorithm == RateLimitAlgorithm.LEAKY_BUCKET:
            return self._check_leaky_bucket(state, config, cost)
        return self._check_sliding_window(state, config, cost)

    def _check_hourly_limit(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
    ) -> tuple:
        """Check hourly limit. Returns (allowed, remaining, reset_after)."""
        if not config.hourly_limit:
            return True, 0, 0.0
        now = time.monotonic()
        hourly_reset = state.get("hourly_reset", now + 3600)
        hourly_count = state.get("hourly_count", 0)
        if now >= hourly_reset:
            hourly_reset = now + 3600
            hourly_count = 0
        if hourly_count + cost <= config.hourly_limit:
            hourly_count += cost
            allowed = True
        else:
            allowed = False
        state["hourly_reset"] = hourly_reset
        state["hourly_count"] = hourly_count
        remaining = max(0, config.hourly_limit - hourly_count)
        reset_after = hourly_reset - now
        return allowed, remaining, reset_after

    def _check_daily_limit(
        self,
        state: Dict[str, Any],
        config: RateLimitConfig,
        cost: int,
    ) -> tuple:
        """Check daily limit. Returns (allowed, remaining, reset_after)."""
        if not config.daily_limit:
            return True, 0, 0.0
        now = time.monotonic()
        daily_reset = state.get("daily_reset", now + 86400)
        daily_count = state.get("daily_count", 0)
        if now >= daily_reset:
            daily_reset = now + 86400
            daily_count = 0
        if daily_count + cost <= config.daily_limit:
            daily_count += cost
            allowed = True
        else:
            allowed = False
        state["daily_reset"] = daily_reset
        state["daily_count"] = daily_count
        remaining = max(0, config.daily_limit - daily_count)
        reset_after = daily_reset - now
        return allowed, remaining, reset_after

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

        Returns:
            RateLimitResult with allowed status and headers.
        """
        now = time.monotonic()
        unix_now = time.time()
        if self._check_bypass(user_id, is_admin, is_internal):
            return self._create_bypass_result(route, unix_now)
        checks = []
        
        # Check global limit (shared by all if no user_id, but we want to avoid that for IPs)
        # If we have an IP but no user, we should probably skip the global shared bucket 
        # or have a separate global IP bucket?
        # For now, let's keep global limit for authenticated users, 
        # and maybe a separate check for IPs if we wanted a "global IP limit".
        # But the requirement is to avoid shared limits for unauthenticated users.
        
        if self._enable_global_limit and user_id is not None:
            checks.append(self._check_global_limit(user_id, cost, unix_now))
            
        if user_id is not None and not is_webhook:
            checks.append(self._check_user_limit(user_id, cost, unix_now))
        elif ip_address is not None and not is_webhook:
            # Fallback to IP limit if no user_id
            checks.append(self._check_ip_limit(ip_address, cost, unix_now))

        if route:
            config = self._get_config_for_request(route, is_bot, is_webhook)
            if config.scope == BucketType.RESOURCE and resource_id is not None:
                checks.append(self._check_resource_limit(
                    user_id, route, resource_id, config, cost, unix_now
                ))
            elif config.scope == BucketType.WEBHOOK and webhook_id is not None:
                checks.append(self._check_webhook_limit(
                    webhook_id, route, resource_id, config, cost, unix_now
                ))
            else:
                # For route limits, we need to pass IP if user_id is missing
                # We need to update _check_route_limit to handle IP
                checks.append(self._check_route_limit(
                    user_id, ip_address, route, config, cost, unix_now
                ))
        for result in checks:
            if not result.allowed:
                return result
        if checks:
            return checks[-1]
        return self._create_bypass_result(route, unix_now)

    def _check_global_limit(
        self,
        user_id: int,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check global rate limit."""
        key = self._generate_bucket_key(BucketType.GLOBAL, user_id=user_id)
        state = self._get_or_create_bucket(key, BucketType.GLOBAL, self._global_config)
        allowed, remaining, reset_after = self._check_algorithm(
            state, self._global_config, cost
        )
        self._storage.set_bucket(key, state, ttl=self._global_config.window_seconds * 2)
        return RateLimitResult(
            allowed=allowed,
            headers=RateLimitHeaders(
                limit=self._global_config.effective_limit,
                remaining=remaining,
                reset=unix_now + reset_after,
                reset_after=reset_after,
                bucket=self._generate_bucket_id(key),
                is_global=True,
                retry_after=reset_after if not allowed else None,
                scope="global",
            ),
            bucket_key=key,
            bucket_type=BucketType.GLOBAL,
            remaining=remaining,
            reset_at=unix_now + reset_after,
            retry_after=reset_after if not allowed else None,
            is_global=True,
            limited_by="global" if not allowed else None,
            cost=cost,
        )

    def _check_user_limit(
        self,
        user_id: int,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check per-user rate limit."""
        key = self._generate_bucket_key(BucketType.USER, user_id=user_id)
        state = self._get_or_create_bucket(key, BucketType.USER, self._user_config)
        allowed, remaining, reset_after = self._check_algorithm(
            state, self._user_config, cost
        )
        if allowed and self._user_config.hourly_limit:
            hourly_allowed, _, hourly_reset = self._check_hourly_limit(
                state, self._user_config, cost
            )
            if not hourly_allowed:
                allowed = False
                reset_after = hourly_reset
        if allowed and self._user_config.daily_limit:
            daily_allowed, _, daily_reset = self._check_daily_limit(
                state, self._user_config, cost
            )
            if not daily_allowed:
                allowed = False
                reset_after = daily_reset
        self._storage.set_bucket(key, state, ttl=86400)
        return RateLimitResult(
            allowed=allowed,
            headers=RateLimitHeaders(
                limit=self._user_config.effective_limit,
                remaining=remaining,
                reset=unix_now + reset_after,
                reset_after=reset_after,
                bucket=self._generate_bucket_id(key),
                is_global=False,
                retry_after=reset_after if not allowed else None,
                scope="user",
            ),
            bucket_key=key,
            bucket_type=BucketType.USER,
            remaining=remaining,
            reset_at=unix_now + reset_after,
            retry_after=reset_after if not allowed else None,
            is_global=False,
            limited_by="user" if not allowed else None,
            cost=cost,
        )

    def _check_ip_limit(
        self,
        ip_address: str,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Check per-IP rate limit (for unauthenticated users)."""
        key = self._generate_bucket_key(BucketType.IP, ip_address=ip_address)
        state = self._get_or_create_bucket(key, BucketType.IP, self._ip_config)
        allowed, remaining, reset_after = self._check_algorithm(
            state, self._ip_config, cost
        )
        if allowed and self._ip_config.hourly_limit:
            hourly_allowed, _, hourly_reset = self._check_hourly_limit(
                state, self._ip_config, cost
            )
            if not hourly_allowed:
                allowed = False
                reset_after = hourly_reset
        if allowed and self._ip_config.daily_limit:
            daily_allowed, _, daily_reset = self._check_daily_limit(
                state, self._ip_config, cost
            )
            if not daily_allowed:
                allowed = False
                reset_after = daily_reset
        self._storage.set_bucket(key, state, ttl=86400)
        return RateLimitResult(
            allowed=allowed,
            headers=RateLimitHeaders(
                limit=self._ip_config.effective_limit,
                remaining=remaining,
                reset=unix_now + reset_after,
                reset_after=reset_after,
                bucket=self._generate_bucket_id(key),
                is_global=False,
                retry_after=reset_after if not allowed else None,
                scope="ip",
            ),
            bucket_key=key,
            bucket_type=BucketType.IP,
            remaining=remaining,
            reset_at=unix_now + reset_after,
            retry_after=reset_after if not allowed else None,
            is_global=False,
            limited_by="ip" if not allowed else None,
            cost=cost,
        )

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
            BucketType.ROUTE, 
            user_id=user_id, 
            ip_address=ip_address,
            route=route
        )
        state = self._get_or_create_bucket(key, BucketType.ROUTE, config)
        allowed, remaining, reset_after = self._check_algorithm(state, config, cost)
        if allowed and config.hourly_limit:
            hourly_allowed, _, hourly_reset = self._check_hourly_limit(state, config, cost)
            if not hourly_allowed:
                allowed = False
                reset_after = hourly_reset
        if allowed and config.daily_limit:
            daily_allowed, _, daily_reset = self._check_daily_limit(state, config, cost)
            if not daily_allowed:
                allowed = False
                reset_after = daily_reset
        self._storage.set_bucket(key, state, ttl=max(config.window_seconds * 2, 3600))
        return RateLimitResult(
            allowed=allowed,
            headers=RateLimitHeaders(
                limit=config.effective_limit,
                remaining=remaining,
                reset=unix_now + reset_after,
                reset_after=reset_after,
                bucket=self._generate_bucket_id(key),
                is_global=False,
                retry_after=reset_after if not allowed else None,
                scope="route",
            ),
            bucket_key=key,
            bucket_type=BucketType.ROUTE,
            remaining=remaining,
            reset_at=unix_now + reset_after,
            retry_after=reset_after if not allowed else None,
            is_global=False,
            limited_by="route" if not allowed else None,
            cost=cost,
        )

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
        state = self._get_or_create_bucket(key, BucketType.RESOURCE, config)
        allowed, remaining, reset_after = self._check_algorithm(state, config, cost)
        self._storage.set_bucket(key, state, ttl=max(config.window_seconds * 2, 60))
        return RateLimitResult(
            allowed=allowed,
            headers=RateLimitHeaders(
                limit=config.effective_limit,
                remaining=remaining,
                reset=unix_now + reset_after,
                reset_after=reset_after,
                bucket=self._generate_bucket_id(key),
                is_global=False,
                retry_after=reset_after if not allowed else None,
                scope="channel",
            ),
            bucket_key=key,
            bucket_type=BucketType.RESOURCE,
            remaining=remaining,
            reset_at=unix_now + reset_after,
            retry_after=reset_after if not allowed else None,
            is_global=False,
            limited_by="resource" if not allowed else None,
            cost=cost,
        )

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
        state = self._get_or_create_bucket(key, BucketType.WEBHOOK, config)
        allowed, remaining, reset_after = self._check_algorithm(state, config, cost)
        if allowed and resource_id is not None:
            channel_key = self._generate_bucket_key(
                BucketType.CHANNEL_WEBHOOK,
                resource_id=resource_id,
            )
            channel_state = self._get_or_create_bucket(
                channel_key, BucketType.CHANNEL_WEBHOOK, DEFAULT_WEBHOOK_CHANNEL_LIMIT
            )
            channel_allowed, _, channel_reset = self._check_algorithm(
                channel_state, DEFAULT_WEBHOOK_CHANNEL_LIMIT, cost
            )
            self._storage.set_bucket(channel_key, channel_state, ttl=120)
            if not channel_allowed:
                allowed = False
                reset_after = channel_reset
        self._storage.set_bucket(key, state, ttl=max(config.window_seconds * 2, 60))
        return RateLimitResult(
            allowed=allowed,
            headers=RateLimitHeaders(
                limit=config.effective_limit,
                remaining=remaining,
                reset=unix_now + reset_after,
                reset_after=reset_after,
                bucket=self._generate_bucket_id(key),
                is_global=False,
                retry_after=reset_after if not allowed else None,
                scope="webhook",
            ),
            bucket_key=key,
            bucket_type=BucketType.WEBHOOK,
            remaining=remaining,
            reset_at=unix_now + reset_after,
            retry_after=reset_after if not allowed else None,
            is_global=False,
            limited_by="webhook" if not allowed else None,
            cost=cost,
        )

    def _create_bypass_result(self, route: Optional[str], unix_now: float) -> RateLimitResult:
        """Create a result for bypassed requests."""
        bucket_id = "bypass"
        return RateLimitResult(
            allowed=True,
            headers=RateLimitHeaders(
                limit=999999,
                remaining=999999,
                reset=unix_now + 60,
                reset_after=60.0,
                bucket=bucket_id,
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
        """Reset all buckets for a user."""
        prefix = f"u:{user_id}"
        keys = self._storage.get_keys_by_prefix("")
        for key in keys:
            if prefix in key:
                self._storage.delete_bucket(key)

    def reset_all(self) -> None:
        """Reset all buckets."""
        self._storage.clear_all()

    def get_bucket_info(self, bucket_key: str) -> Optional[RateLimitBucket]:
        """Get bucket information."""
        state = self._storage.get_bucket(bucket_key)
        if state is None:
            return None
        config = get_default_config()
        return RateLimitBucket(
            key=state.get("key", bucket_key),
            bucket_type=BucketType(state.get("bucket_type", "route")),
            config=config,
            tokens=state.get("tokens", 0),
            last_update=state.get("last_update", 0),
            window_start=state.get("window_start", 0),
            request_count=state.get("request_count", 0),
            hourly_count=state.get("hourly_count", 0),
            hourly_reset=state.get("hourly_reset", 0),
            daily_count=state.get("daily_count", 0),
            daily_reset=state.get("daily_reset", 0),
            request_timestamps=state.get("timestamps", []),
        )
