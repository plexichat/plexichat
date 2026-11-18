"""Bounded, worker-aware connection-pool configuration.

``docker compose up --scale backend=N`` starts N independent backend
processes, each with its own pool. The previous code handed every process the
full ``connection_pool.max_connections`` (100 by default), so N replicas tried
to open N*100 sockets against one PostgreSQL instance whose own
``max_connections`` is 100. The surplus were refused, workers queued on an
exhausted pool, and the deployment fell over under load.

This module converts one fleet-wide ceiling into a bounded per-worker ceiling
and clamps the acquisition timeout so a saturated pool surfaces an error
instead of parking request threads forever. No value here may be unbounded:
the inputs come from config or the environment, both of which may be
mis-typed, negative, or hostile.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from src.core.security.hardening import HardeningViolation, bounded_int

#: PostgreSQL's default ``max_connections`` is 100. Keep headroom for the
#: migration job, the backup container and ad-min psql sessions.
DEFAULT_TOTAL_CONNECTION_BUDGET = 90

#: Anything beyond this is a misconfiguration, not a replica fleet.
MAX_REPLICAS = 64
MAX_POOL_SIZE = 10_000
MIN_PER_WORKER_CONNECTIONS = 2

DEFAULT_ACQUIRE_TIMEOUT_SECONDS = 30.0
MAX_ACQUIRE_TIMEOUT_SECONDS = 300.0

#: Environment variables carrying the replica count, in priority order.
_REPLICA_ENV_VARS = ("BACKEND_REPLICAS", "WEB_CONCURRENCY", "UVICORN_WORKERS")


@dataclass(frozen=True)
class PoolLimits:
    """Bounded min/max connections for a single worker process."""

    min_connections: int
    max_connections: int


def _bounded_int(
    value: Any, field: str, *, minimum: int, maximum: int, default: int
) -> int:
    try:
        return bounded_int(value, field, minimum=minimum, maximum=maximum)
    except (HardeningViolation, TypeError, ValueError):
        return default


def _bounded_float(
    value: Any, field: str, *, minimum: float, maximum: float, default: float
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if number != number or number in (float("inf"), float("-inf")):
        return default
    return min(max(number, minimum), maximum)


def _pool_config(config: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    if not isinstance(config, Mapping):
        return {}
    pool_config = config.get("connection_pool", {})
    return pool_config if isinstance(pool_config, Mapping) else {}


def _replica_count(pool_config: Mapping[str, Any], env: Mapping[str, str]) -> int:
    for name in _REPLICA_ENV_VARS:
        raw = env.get(name)
        if raw is not None and str(raw).strip():
            return _bounded_int(raw, name, minimum=1, maximum=MAX_REPLICAS, default=1)
    return _bounded_int(
        pool_config.get("replicas", 1),
        "connection_pool.replicas",
        minimum=1,
        maximum=MAX_REPLICAS,
        default=1,
    )


def resolve_pool_limits(
    config: Optional[Mapping[str, Any]],
    *,
    requested_min: Optional[Any] = None,
    requested_max: Optional[Any] = None,
    env: Optional[Mapping[str, str]] = None,
) -> PoolLimits:
    """Return bounded per-worker min/max connections for ``config``.

    ``requested_min``/``requested_max`` are the values a caller derived from
    the same config; they are used only when the config itself omits a key.
    ``env`` defaults to ``os.environ`` and is injectable for tests.
    """
    pool_config = _pool_config(config)
    environ: Mapping[str, str] = os.environ if env is None else env

    replicas = _replica_count(pool_config, environ)
    total_budget = _bounded_int(
        environ.get("DATABASE_TOTAL_CONNECTION_BUDGET")
        or pool_config.get("total_connection_budget")
        or DEFAULT_TOTAL_CONNECTION_BUDGET,
        "total_connection_budget",
        minimum=MIN_PER_WORKER_CONNECTIONS,
        maximum=MAX_POOL_SIZE,
        default=DEFAULT_TOTAL_CONNECTION_BUDGET,
    )

    if "max_connections" in pool_config:
        max_source = pool_config["max_connections"]
    elif requested_max is not None:
        max_source = requested_max
    else:
        max_source = 100
    configured_max = _bounded_int(
        max_source,
        "max_connections",
        minimum=1,
        maximum=MAX_POOL_SIZE,
        default=100,
    )
    # The fleet budget is a hard ceiling; a single worker may still use fewer
    # connections than the budget allows when the operator set a lower max.
    per_worker_max = min(
        configured_max, max(MIN_PER_WORKER_CONNECTIONS, total_budget // replicas)
    )

    if "min_connections" in pool_config:
        min_source = pool_config["min_connections"]
    elif requested_min is not None:
        min_source = requested_min
    else:
        min_source = 5
    configured_min = _bounded_int(
        min_source,
        "min_connections",
        minimum=0,
        maximum=MAX_POOL_SIZE,
        default=5,
    )
    # ``min_connections`` are opened eagerly by the pool, so they multiply by
    # replica count too. Shrink the warm pool alongside the ceiling and keep
    # the pair ordered so pool construction cannot fail with min > max.
    per_worker_min = min(configured_min, per_worker_max)

    return PoolLimits(min_connections=per_worker_min, max_connections=per_worker_max)


def resolve_connect_timeout(
    config: Optional[Mapping[str, Any]],
    *,
    default: float = DEFAULT_ACQUIRE_TIMEOUT_SECONDS,
) -> float:
    """Return a bounded connect/acquire timeout in seconds.

    An unbounded value parks a request thread forever when the database or
    the pool is saturated; a negative one makes every acquisition fail
    immediately. Both are clamped here at the config boundary.
    """
    pool_config = _pool_config(config)
    return _bounded_float(
        pool_config.get("connect_timeout", default),
        "connect_timeout",
        minimum=0.0,
        maximum=MAX_ACQUIRE_TIMEOUT_SECONDS,
        default=default,
    )
