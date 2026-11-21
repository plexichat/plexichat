"""Module-cached derived security policy with safe invalidation.

Several middleware layers rebuild the same derived values on every request:
the configured API prefix, the set of public endpoints under that prefix, the
docs path and the prefix-scoped path fragments used for webhook/password
gating. These are static until configuration changes, but re-deriving them
adds config reads and string building to every request in the hot band.

The values are cached in a :class:`VersionedConfigSnapshot`, so an unchanged
configuration is served from memory and an edit (or a new config loader) is
picked up on the very next access. The cache key also includes the identity of
the API config object, which makes monkeypatched configs in tests invalidate
the cache immediately instead of leaking a stale policy into a later test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, FrozenSet, Optional, cast

from src.core.config.snapshot import VersionedConfigSnapshot, file_version

_DEFAULT_PREFIX = "/api/v1"


@dataclass(frozen=True)
class StaticPolicy:
    """Derived, request-invariant routing policy."""

    api_prefix: str
    public_endpoints: FrozenSet[str]
    docs_path: str
    password_requirements_prefix: str
    webhook_execute_prefix: str


def _api_config():
    """Fetch the API config without importing it at module import time.

    Late binding keeps patches to ``src.api.config.get_api_config`` effective
    (the same call sites the middleware historically used).
    """
    import src.api.config as api_config

    return api_config.get_api_config()


def _config_version():
    """Cheap token that changes whenever any policy input changes."""
    loader = None
    path = None
    try:
        import utils.config as config

        loader = getattr(config, "_config_instance", None)
        path = getattr(loader, "config_path", None)
    except Exception:
        pass
    try:
        api_cfg = _api_config()
    except Exception:
        api_cfg = None
    return (
        id(loader),
        file_version(path),
        # Value-based, not identity-based: get_api_config() hands out a
        # detached copy on every read, so id(api_cfg) would differ on each
        # version check and force a needless rebuild even with no config
        # change. repr() is stable for equal APIConfig values.
        repr(api_cfg),
    )


def _build_policy() -> StaticPolicy:
    try:
        prefix = _api_config().api_prefix.rstrip("/") or _DEFAULT_PREFIX
    except Exception:
        prefix = _DEFAULT_PREFIX

    # Keep the literal (prefix-independent) entries in sync with the public
    # endpoint list in the authentication middleware.
    public = {
        f"{prefix}/auth/login",
        f"{prefix}/auth/register",
        f"{prefix}/auth/verify-email",
        f"{prefix}/auth/resend-verification",
        f"{prefix}/status",
        f"{prefix}/health",
        f"{prefix}/capabilities",
        f"{prefix}/version",
        f"{prefix}/auth/password-requirements",
        f"{prefix}/telemetry/csp-report",
        f"{prefix}/public/license/info",
        f"{prefix}/public/license/challenge",
        f"{prefix}/public/license/export",
        "/health",
        "/status",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/",
    }

    docs_path: Optional[str] = None
    try:
        from src.api.routes.docs import get_docs_config

        candidate = get_docs_config().path or "/docs/api"
        candidate = candidate if candidate.startswith("/") else f"/{candidate}"
        docs_path = candidate.rstrip("/")
    except Exception:
        docs_path = None

    return StaticPolicy(
        api_prefix=prefix,
        public_endpoints=frozenset(public),
        docs_path=docs_path or "",
        password_requirements_prefix=f"{prefix}/auth/password-requirements",
        webhook_execute_prefix=f"{prefix}/webhooks/",
    )


# ``get_static_policy()`` is the first call the authentication middleware makes
# on every HTTP request.  Its version token stats the config file, so repeating
# that check on every request is needless filesystem traffic on the hottest
# path.  A short staleness ceiling throttles the stat to at most once per
# window, matching the pattern used by the API-config snapshot.  Version
# identity (loader id, file mtime/size, API-config identity) still governs
# rebuilds, so an on-disk edit or ``config.setup()`` is observed on the next
# check after the ceiling and ``clear_static_policy_cache()`` stays immediate.
_POLICY_MAX_AGE_SECONDS = 5.0

_policy_snapshot = VersionedConfigSnapshot(
    cast(Callable[[], Dict[str, Any]], _build_policy),
    version_fn=_config_version,
    max_age=_POLICY_MAX_AGE_SECONDS,
)


def get_static_policy() -> StaticPolicy:
    """Return the current derived policy, rebuilding only when inputs change."""
    return cast(StaticPolicy, _policy_snapshot.snapshot())


def warm_static_policy_cache() -> None:
    """Prime the derived-policy snapshot off the request path (best-effort).

    ``get_static_policy()`` is the first call the authentication middleware
    makes on every HTTP request; on a cold process that call builds the whole
    policy (config reads, prefix derivation, docs-path resolution). Building it
    once here, after the server is listening, moves that one-off cost onto the
    worker thread so the first real request is a cache hit. Only the shared,
    request-invariant policy is primed: every authentication/authorization gate
    in the middleware still runs on each request exactly as before. Failures
    are logged and swallowed - the middleware derives the policy on demand if
    this did not run.
    """
    try:
        _policy_snapshot.snapshot()
    except Exception as e:
        try:
            import utils.logger as logger

            logger.debug(f"Startup warm-up static policy skipped: {e}")
        except Exception:
            pass


def clear_static_policy_cache() -> None:
    """Drop the cached policy so the next access re-derives it."""
    _policy_snapshot.invalidate()


__all__ = [
    "StaticPolicy",
    "clear_static_policy_cache",
    "get_static_policy",
    "warm_static_policy_cache",
]
