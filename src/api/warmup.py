"""
Boot-time module warm-up - Background task started from the app lifespan.

The first request to a fan-out read path after a restart pays a cost that no
later request pays: the module behind it initialises lazily on first use, the
first pooled DB connection is opened, and the response cache is empty. On the
a214 deployment ``GET /features/users/:id/profile`` took 2321ms as the first
call after the restart and 12.9ms on every call after it; ``GET
/search/messages`` went 989ms -> 100ms and ``GET /admin/users`` 841ms -> 11ms.

Initialising those modules once, after the server is listening, moves that
cold hit off the request path and onto a worker thread, so the first real
user does not pay it. Everything here is best-effort: a module that fails to
warm must never fail startup, and the request path still initialises on
demand exactly as before.
"""

import asyncio
import time
from typing import Any, Optional

import utils.config as config
import utils.logger as logger

# Bounded so a misconfigured value cannot park the task for hours or run it
# while the rest of startup (static client, presence sweep) is still settling.
_MAX_DELAY_SECONDS = 30.0

# Modules behind the read paths that showed a cold first hit in the live log.
# Order matters only in that the database is touched last, once everything
# that needs a connection has actually opened one.
_WARM_GETTERS = (
    "auth",
    "servers",
    "messaging",
    "search",
    "features",
    "media",
    "admin",
    "settings",
    "presence",
)


def _warmup_enabled() -> bool:
    try:
        return bool(config.get("api.warmup_enabled", True))
    except Exception:
        return True


def _warmup_delay() -> float:
    try:
        delay = float(config.get("api.warmup_delay_seconds", 2.0))
    except (TypeError, ValueError):
        return 2.0
    return max(0.0, min(delay, _MAX_DELAY_SECONDS))


def _db_probe(api_mod: Any) -> None:
    """Open the first pooled connection and compile one statement here.

    A missing or differently-shaped database object is not an error: the
    caller wraps this, and the request path would have failed the same way.
    """
    db = api_mod.get_database()
    if db is None:
        return
    fetch_one = getattr(db, "fetch_one", None)
    if not callable(fetch_one):
        return
    try:
        fetch_one("SELECT 1")
    except TypeError:
        # Some adapters require the parameter tuple explicitly.
        fetch_one("SELECT 1", ())


def _warm_profile_path(api_mod: Any) -> None:
    """Import and exercise the profile read path behind a profile GET.

    ``GET /features/users/:id/profile`` imports ``src.core.profiles.manager``
    lazily inside the handler and then runs one indexed query; on a215 the first
    call after a restart still cost ~1s while every later call was milliseconds,
    so the module import and the first statement compile belong here, not on a
    user's request.
    """
    from src.core.profiles.manager import ProfileManager

    db = api_mod.get_database()
    if db is None:
        return
    # user id 0 has no row, so this is a pure read of the default shape.
    ProfileManager(db).get_profile(0)


def _warm_security_policy() -> None:
    """Prime the derived security-policy cache off-request.

    The authentication middleware calls ``get_static_policy()`` as its first
    step on every HTTP request. On a cold process that call builds the policy
    (config reads, prefix derivation, docs-path resolution) on the request
    path. Warming it here moves that one-off build onto this worker thread.
    Only the request-invariant policy is primed: every auth gate downstream
    still runs on each request, and a failure here is non-fatal - the
    middleware falls back to deriving the policy on demand.
    """
    try:
        from src.api.middleware.policy_cache import warm_static_policy_cache

        warm_static_policy_cache()
    except Exception as e:
        logger.debug(f"Startup warm-up security policy failed: {e}")


def _warm_capability_catalogs() -> None:
    """Prime the launcher and admin capability payload caches off-request.

    Both caches hold the fully evaluated capability catalog; the first build
    runs every evaluator, including the voice-transcription provider probe that
    imports the provider factory on a cold process. They are process-wide,
    short-TTL, single-entry caches, so warming them is bounded work. This runs
    on the same worker thread as the rest of ``warm_once`` and never touches the
    per-request auth gates, which continue to run before either cached payload
    can be served.
    """
    try:
        from src.api.routes import capabilities as launcher_caps

        launcher_caps.warm_capabilities_payload_cache()
    except Exception as e:
        logger.debug(f"Startup warm-up launcher catalog skipped: {e}")
    try:
        from src.api.routes.admin import capabilities as admin_caps

        admin_caps.warm_admin_capabilities_payload_cache()
    except Exception as e:
        logger.debug(f"Startup warm-up admin catalog skipped: {e}")


def _warm_openapi(app: Any) -> None:
    """Generate and cache the OpenAPI schema off the request path."""
    if app is None:
        return
    try:
        app.openapi()
        from src.api.openapi_cache import save_cached_openapi

        save_cached_openapi(app)
    except Exception as e:
        logger.debug(f"Startup warm-up OpenAPI generation failed: {e}")


def warm_once(app: Any = None) -> float:
    """Initialise the hot modules once and return the elapsed milliseconds.

    Never raises: warm-up is an optimisation, and a failure here must not
    turn into a startup failure. Each step is independent so one broken
    module cannot prevent the others from warming.
    """
    import src.api as api

    started = time.perf_counter()
    for name in _WARM_GETTERS:
        getter = getattr(api, f"get_{name}", None)
        if not callable(getter):
            continue
        try:
            getter()
        except Exception as e:
            logger.debug(f"Startup warm-up skipped module '{name}': {e}")

    try:
        _warm_profile_path(api)
    except Exception as e:
        logger.debug(f"Startup warm-up profile probe failed: {e}")

    try:
        _warm_security_policy()
    except Exception as e:
        logger.debug(f"Startup warm-up security policy failed: {e}")

    try:
        _warm_capability_catalogs()
    except Exception as e:
        logger.debug(f"Startup warm-up capability catalogs failed: {e}")

    try:
        _db_probe(api)
    except Exception as e:
        logger.debug(f"Startup warm-up DB probe failed: {e}")

    _warm_openapi(app)

    return (time.perf_counter() - started) * 1000.0


async def warmup_loop(app: Any = None) -> None:
    if not _warmup_enabled():
        logger.debug("Startup warm-up disabled by config")
        return
    try:
        await asyncio.sleep(_warmup_delay())
        elapsed = await asyncio.get_running_loop().run_in_executor(None, warm_once, app)
        logger.info(f"Startup warm-up completed in {elapsed:.0f}ms")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Startup warm-up failed: {e}")


def start_warmup(app: Any = None) -> asyncio.Task:
    """Start the warm-up task; call from the running event loop (lifespan)."""
    return asyncio.create_task(warmup_loop(app))


async def stop_warmup(task: Optional[asyncio.Task]) -> None:
    """Cancel and await the warm-up task; never raises."""
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Startup warm-up stop error: {e}")
