"""
Periodic media-maintenance sweep - Background task started from the app lifespan.

Two TTL sweeps that must not depend on request traffic:

* Expired chunked-upload sessions (and their temp files) - previously
  only reaped when a request happened to touch the session, so abandoned
  uploads consumed temp storage until a client came back.
* Webhook retry-queue cleanup piggybacks on the delivery worker cycle
  (see WebhookDeliveryJob.run_once); it is deliberately NOT duplicated
  here to avoid two workers racing the same table.
"""

import asyncio

import utils.config as config
import utils.logger as logger

_SWEEP_MIN_INTERVAL_SEC = 30.0


async def _sweep_once() -> None:
    import src.api as api_module  # lazy: avoid import cycles at module load

    media = api_module.get_media()
    if media is not None and hasattr(media, "cleanup_expired_upload_sessions"):
        cleaned = await asyncio.get_event_loop().run_in_executor(
            None, media.cleanup_expired_upload_sessions
        )
        if cleaned:
            logger.info(f"Chunked-upload sweep removed {cleaned} expired session(s)")


async def media_maintenance_sweep_loop() -> None:
    interval_cfg = config.get("media.chunked_upload.cleanup_interval", 300)
    try:
        interval = float(interval_cfg)
    except (TypeError, ValueError):
        interval = 300.0
    interval = max(interval, _SWEEP_MIN_INTERVAL_SEC)
    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Chunked-upload maintenance sweep failed: {e}")
        await asyncio.sleep(interval)


def start_media_maintenance_sweep() -> asyncio.Task:
    """Start the sweep task; call from the running event loop (lifespan)."""
    return asyncio.create_task(media_maintenance_sweep_loop())


async def stop_media_maintenance_sweep(task: asyncio.Task) -> None:
    """Cancel and await the sweep task; never raises."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Media maintenance sweep stop error: {e}")
