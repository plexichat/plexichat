"""
Static client service.

Background task that keeps the active web client install in sync with the
server's version. Started from the FastAPI server lifecycle so it runs as
part of normal startup and is cancelled at shutdown.

The service is intentionally simple: it delegates all real work to
:class:`StaticClientManager.maybe_check`, which is idempotent and rate-limited
by ``auto_update_check_interval_seconds`` on the manager itself.
"""

from __future__ import annotations

import asyncio
from typing import Optional

import utils.logger as logger

from .manager import StaticClientManager, get_static_client_manager


_task: Optional[asyncio.Task] = None
_stop_event: Optional[asyncio.Event] = None


async def _run_loop(manager: StaticClientManager, stop_event: asyncio.Event) -> None:
    """Run the periodic auto-update loop until *stop_event* is set."""
    interval = max(15, int(manager.config.auto_update_check_interval_seconds or 0))
    logger.info(f"static_client: background loop interval={interval}s")
    while not stop_event.is_set():
        try:
            result = await asyncio.to_thread(manager.maybe_check)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"static_client: background check raised: {exc}")
        else:
            if result.error and not result.already_current:
                logger.warning(f"static_client: background check error: {result.error}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
        except asyncio.CancelledError:
            break


async def start_static_client_service() -> Optional[asyncio.Task]:
    """Start the background auto-update task.

    Returns the running :class:`asyncio.Task`, or ``None`` if the feature is
    disabled or the manager could not be initialised.
    """
    global _task, _stop_event
    if _task is not None and not _task.done():
        return _task
    mgr = get_static_client_manager()
    if mgr is None or not mgr.config.enabled:
        return None
    if not mgr.config.serve and mgr.config.source != "gitlab_release":
        return None
    _stop_event = asyncio.Event()
    _task = asyncio.create_task(
        _run_loop(mgr, _stop_event), name="plexichat.static_client.autoupdate"
    )
    logger.info("static_client: background auto-update task started")
    return _task


async def stop_static_client_service() -> None:
    """Signal the background task to stop and await its termination."""
    global _task, _stop_event
    if _stop_event is not None:
        _stop_event.set()
    if _task is None:
        return
    try:
        await asyncio.wait_for(_task, timeout=10)
    except (asyncio.TimeoutError, asyncio.CancelledError):
        _task.cancel()
    finally:
        _task = None
        _stop_event = None


def run_static_client_initial_install() -> None:
    """Run a synchronous ``ensure_active`` so the first request is servable.

    Safe to call from the startup hook. Logs and swallows all errors.
    Also re-issues the runtime config.js so template overrides in
    ``config.yaml`` take effect even when no new install is needed.
    """
    mgr = get_static_client_manager()
    if mgr is None or not mgr.config.enabled:
        return
    try:
        result = mgr.ensure_active()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"static_client: initial install raised: {exc}")
        return
    if result.error and not result.already_current:
        logger.warning(f"static_client: initial install error: {result.error}")
    try:
        if mgr.reissue_runtime_config():
            logger.info("static_client: re-issued runtime config.js for active install")
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"static_client: reissue config raised: {exc}")


__all__ = [
    "start_static_client_service",
    "stop_static_client_service",
    "run_static_client_initial_install",
]
