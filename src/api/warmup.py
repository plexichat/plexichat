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


def warm_once() -> float:
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
        _db_probe(api)
    except Exception as e:
        logger.debug(f"Startup warm-up DB probe failed: {e}")

    return (time.perf_counter() - started) * 1000.0


async def warmup_loop() -> None:
    if not _warmup_enabled():
        logger.debug("Startup warm-up disabled by config")
        return
    try:
        await asyncio.sleep(_warmup_delay())
        elapsed = await asyncio.get_running_loop().run_in_executor(None, warm_once)
        logger.info(f"Startup warm-up completed in {elapsed:.0f}ms")
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning(f"Startup warm-up failed: {e}")


def start_warmup() -> asyncio.Task:
    """Start the warm-up task; call from the running event loop (lifespan)."""
    return asyncio.create_task(warmup_loop())


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
