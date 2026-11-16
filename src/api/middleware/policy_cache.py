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
from typing import FrozenSet, Optional

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
        id(api_cfg),
        getattr(api_cfg, "api_prefix", None),
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


_policy_snapshot = VersionedConfigSnapshot(_build_policy, version_fn=_config_version)


def get_static_policy() -> StaticPolicy:
    """Return the current derived policy, rebuilding only when inputs change."""
    return _policy_snapshot.snapshot()


def clear_static_policy_cache() -> None:
    """Drop the cached policy so the next access re-derives it."""
    _policy_snapshot.invalidate()


__all__ = ["StaticPolicy", "clear_static_policy_cache", "get_static_policy"]
