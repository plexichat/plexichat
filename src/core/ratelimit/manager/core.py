"""Rate limit manager facade.

The manager owns configuration, per-scope bucket selection, and the public
sync/async APIs. Storage round trips are handled by :mod:`atomic`; pure
algorithm code lives in :mod:`algorithms`; keys, results, validation, bypass
decisions, and custom counters each have their own module.
"""

from dataclasses import replace
import re
from typing import Any, Callable, Dict, List, Optional

from ..config import (
    DEFAULT_ROUTE_LIMITS,
    DEFAULT_WEBHOOK_CHANNEL_LIMIT,
    get_default_config,
    get_global_limit,
    get_ip_limit,
    get_user_limit,
    is_bot_higher_limit_route,
    merge_route_configs,
)
from ..models import (
    BucketType,
    RateLimitBucket,
    RateLimitConfig,
    RateLimitResult,
)
from ..storage import MemoryStorage, RateLimitStorage
from ._clock import now as clock_now
from .atomic import check_atomic_async, check_atomic_sync
from .bypass import BypassCheck, should_bypass
from .constants import MAX_ROUTE_KEY_LENGTH
from .counters import apply_custom_increment
from .keys import generate_bucket_id, generate_bucket_key
from .results import bucket_from_state, build_result, create_bypass_result
from .validation import (
    normalise_amount,
    normalise_cost,
    validate_bypass_check,
    validate_config,
    validate_custom_counter_args,
    validate_multiplier,
)


class RateLimitManager:
    """Manages rate limit checking and bucket state with atomic operations."""

    #: Bound the route component of bucket keys (defence in depth; the
    #: middleware already normalizes paths before they reach the manager).
    MAX_ROUTE_KEY_LENGTH = MAX_ROUTE_KEY_LENGTH

    def __init__(
        self,
        storage_backend: Optional[RateLimitStorage] = None,
        route_configs: Optional[Dict[str, RateLimitConfig]] = None,
        global_config: Optional[RateLimitConfig] = None,
        user_config: Optional[RateLimitConfig] = None,
        ip_config: Optional[RateLimitConfig] = None,
        bot_multiplier: float = 1.5,
        webhook_multiplier: float = 1.0,
        bypass_check: Optional[Callable[..., bool]] = None,
        enable_global_limit: bool = True,
    ):
        """Initialize rate limit manager."""
        self._storage = storage_backend or MemoryStorage()

        merged_routes = merge_route_configs(DEFAULT_ROUTE_LIMITS, route_configs or {})
        self._route_configs = {
            route: validate_config(
                replace(config),
                name=f"route_configs[{route!r}]",
            )
            for route, config in merged_routes.items()
        }
        self._global_config = validate_config(
            replace(global_config or get_global_limit()), name="global_config"
        )
        self._user_config = validate_config(
            replace(user_config or get_user_limit()), name="user_config"
        )
        self._ip_config = validate_config(
            replace(ip_config or get_ip_limit()), name="ip_config"
        )
        self._webhook_channel_config = validate_config(
            replace(DEFAULT_WEBHOOK_CHANNEL_LIMIT),
            name="webhook_channel_limit",
        )
        self._bot_multiplier = validate_multiplier(bot_multiplier, "bot_multiplier")
        self._webhook_multiplier = validate_multiplier(
            webhook_multiplier, "webhook_multiplier"
        )

        if bypass_check is not None:
            validate_bypass_check(bypass_check)
        self._bypass_check: Optional[BypassCheck] = bypass_check
        self._enable_global_limit = bool(enable_global_limit)

    def set_bypass_check(self, bypass_check: Callable[..., bool]) -> None:
        """Set the bypass check function."""
        validate_bypass_check(bypass_check)
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
        """Generate a unique, bounded bucket key."""
        return generate_bucket_key(
            bucket_type,
            user_id=user_id,
            ip_address=ip_address,
            route=route,
            resource_id=resource_id,
            webhook_id=webhook_id,
        )

    def _generate_bucket_id(self, key: str) -> str:
        """Generate a short bucket ID for headers."""
        return generate_bucket_id(key)

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
        """Assemble a :class:`RateLimitResult` from a raw bucket outcome."""
        return build_result(
            key,
            bucket_type,
            rl_config,
            allowed,
            remaining,
            reset_after,
            unix_now,
            cost,
        )

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
            config = validate_config(replace(override_config), name="override_config")
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
        """Check if request should bypass rate limiting."""
        return should_bypass(
            self._bypass_check,
            user_id,
            is_admin,
            is_internal,
        )

    def _check_atomic(
        self,
        key: str,
        bucket_type: BucketType,
        rl_config: RateLimitConfig,
        cost: int,
        unix_now: float,
    ) -> RateLimitResult:
        """Evaluate a bucket using atomic storage operations if possible."""
        return check_atomic_sync(
            self._storage,
            key,
            bucket_type,
            rl_config,
            cost,
            unix_now,
        )

    def _bucket_from_state(
        self, state: Dict[str, Any], bucket_key: str
    ) -> RateLimitBucket:
        """Build a :class:`RateLimitBucket` from a raw state dict."""
        return bucket_from_state(state, bucket_key)

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
        cost = normalise_cost(cost)
        unix_now = clock_now()
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

        # Return the most specific successful result (usually the
        # route/resource limit) to provide the most useful headers.
        return checks[-1] if checks else self._create_bypass_result(route, unix_now)

    def increment_custom_usage(
        self, key: str, field: str, amount: int, ttl: int = 86400
    ) -> int:
        """
        Increment a custom usage counter directly in storage.
        Useful for non-request metrics like daily upload size.
        """
        validate_custom_counter_args(key, field, ttl)
        value = normalise_amount(amount)
        outcome: Dict[str, int] = {}

        def mutate(state: Dict[str, Any]) -> None:
            apply_custom_increment(state, field, value, outcome)

        self._storage.update_bucket(key, mutate, ttl=ttl)
        return outcome.get("value", value)

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
            BucketType.ROUTE,
            user_id=user_id,
            ip_address=ip_address,
            route=route,
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
                self._webhook_channel_config,
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
        return create_bypass_result(route, unix_now)

    def get_headers(self, result: RateLimitResult) -> Dict[str, str]:
        """Get HTTP headers from a rate limit result."""
        return result.headers.to_dict()

    def reset_bucket(self, bucket_key: str) -> None:
        """Reset a specific bucket."""
        if not isinstance(bucket_key, str) or not bucket_key:
            raise ValueError("bucket_key must be a non-empty string")
        self._storage.delete_bucket(bucket_key)

    def reset_user(self, user_id: int) -> None:
        """Reset all buckets for a user.

        SECURITY: matching must be an EXACT segment match on the
        ``u:{id}`` key component. The previous ``prefix in key``
        substring check deleted buckets of unrelated users (user 1
        also matched users 10..19, 100..199, and any resource-scoped
        key merely containing ``u:1``).
        """
        pattern = _user_segment_pattern(user_id)
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
    # result assembly -- is shared through the helpers, so the two paths
    # cannot diverge; only the storage round trips differ.

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
        cost = normalise_cost(cost)
        unix_now = clock_now()
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
        return await check_atomic_async(
            self._storage,
            key,
            bucket_type,
            rl_config,
            cost,
            unix_now,
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
            BucketType.ROUTE,
            user_id=user_id,
            ip_address=ip_address,
            route=route,
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
                self._webhook_channel_config,
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
        validate_custom_counter_args(key, field, ttl)
        value = normalise_amount(amount)
        outcome: Dict[str, int] = {}

        def mutate(state: Dict[str, Any]) -> None:
            apply_custom_increment(state, field, value, outcome)

        await self._storage.aupdate_bucket(key, mutate, ttl=ttl)
        return outcome.get("value", value)

    async def areset_bucket(self, bucket_key: str) -> None:
        """Awaitable counterpart of :meth:`reset_bucket`."""
        if not isinstance(bucket_key, str) or not bucket_key:
            raise ValueError("bucket_key must be a non-empty string")
        await self._storage.adelete_bucket(bucket_key)

    async def areset_user(self, user_id: int) -> None:
        """Awaitable counterpart of :meth:`reset_user`.

        Uses the same EXACT-segment ``u:{id}`` match as the blocking form.
        """
        pattern = _user_segment_pattern(user_id)
        keys = await self._storage.aget_keys_by_prefix("")
        for key in keys:
            if pattern.search(key):
                await self._storage.adelete_bucket(key)

    async def areset_all(self) -> None:
        """Awaitable counterpart of :meth:`reset_all`."""
        await self._storage.aclear_all()


def _user_segment_pattern(user_id: int) -> re.Pattern[str]:
    """Compile the exact ``u:{id}`` segment matcher used by reset_user."""
    return re.compile(rf"(?:^|:)u:{re.escape(str(user_id))}(?=$|:)")
