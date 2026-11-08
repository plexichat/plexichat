"""Pure rate-limit algorithms.

These functions mutate a bucket-state dictionary in place and return the
outcome. Keeping them free of storage and manager dependencies makes them
straightforward to test and impossible to accidentally call with a live
backend reference.
"""

from typing import Any, Dict, Optional

from ..models import RateLimitAlgorithm, RateLimitConfig


def fixed_window(
    state: Dict[str, Any],
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> tuple[bool, int, float]:
    """Evaluate a fixed-window bucket."""
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


def sliding_window(
    state: Dict[str, Any],
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> tuple[bool, int, float]:
    """Evaluate a sliding-window bucket."""
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


def leaky_bucket(
    state: Dict[str, Any],
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> tuple[bool, int, float]:
    """Evaluate a leaky-bucket bucket."""
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


def check_hourly_limit(
    state: Dict[str, Any],
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> tuple[bool, int, float]:
    """Check a bucket's hourly limit."""
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


def check_daily_limit(
    state: Dict[str, Any],
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> tuple[bool, int, float]:
    """Check a bucket's daily limit."""
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


def apply_secondary_limits(
    state: Dict[str, Any],
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> Optional[float]:
    """Apply hourly/daily limits to a token bucket under the storage lock.

    Returns the reset-after value when a secondary limit denies the request,
    or ``None`` when the request is still allowed. On denial the already
    consumed primary token is rolled back so a near-cap request does not burn
    quota without succeeding.
    """
    secondary_reset_after: Optional[float] = None

    if config.hourly_limit:
        hourly_allowed, _, hourly_reset = check_hourly_limit(
            state, config, cost, unix_now
        )
        if not hourly_allowed:
            secondary_reset_after = hourly_reset

    if secondary_reset_after is None and config.daily_limit:
        daily_allowed, _, daily_reset = check_daily_limit(state, config, cost, unix_now)
        if not daily_allowed:
            secondary_reset_after = daily_reset

    if secondary_reset_after is not None:
        tokens = float(state.get("tokens", 0.0))
        state["tokens"] = min(float(config.effective_limit), tokens + cost)

    return secondary_reset_after


def apply_legacy_check(
    state: Dict[str, Any],
    config: RateLimitConfig,
    cost: int,
    unix_now: float,
) -> tuple[bool, int, float]:
    """Apply a non-token-bucket algorithm plus its secondary limits."""
    if config.algorithm == RateLimitAlgorithm.FIXED_WINDOW:
        allowed, remaining, reset_after = fixed_window(state, config, cost, unix_now)
    elif config.algorithm == RateLimitAlgorithm.LEAKY_BUCKET:
        allowed, remaining, reset_after = leaky_bucket(state, config, cost, unix_now)
    else:
        allowed, remaining, reset_after = sliding_window(state, config, cost, unix_now)

    if allowed and (config.hourly_limit or config.daily_limit):
        if config.hourly_limit:
            hourly_allowed, _, hourly_reset = check_hourly_limit(
                state, config, cost, unix_now
            )
            if not hourly_allowed:
                allowed = False
                reset_after = hourly_reset
        if allowed and config.daily_limit:
            daily_allowed, _, daily_reset = check_daily_limit(
                state, config, cost, unix_now
            )
            if not daily_allowed:
                allowed = False
                reset_after = daily_reset

    return allowed, remaining, reset_after
