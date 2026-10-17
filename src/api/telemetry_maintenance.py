"""
Periodic telemetry retention sweep - Background task started from app lifespan.

``cleanup_old_data`` was never invoked by anything, so telemetry rows grew
without bound despite the documented retention period. This bounded-interval
sweep deletes measurements older than ``telemetry.retention_days`` on a
schedule, consistent with the poll-expiry/media-maintenance sweeps.
"""

import asyncio

import utils.config as config
import utils.logger as logger

_MIN_SWEEP_INTERVAL_SEC = 60.0


async def telemetry_retention_sweep_loop() -> None:
    from src.core import telemetry as telemetry_module

    interval = float(config.get("telemetry.retention_sweep_interval_seconds", 3600))
    interval = max(interval, _MIN_SWEEP_INTERVAL_SEC)
    while True:
        try:
            if telemetry_module.is_setup():
                days = int(config.get("telemetry.retention_days", 30))
                deleted = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: telemetry_module.cleanup_old_data(days=days)
                )
                if deleted:
                    logger.info(
                        f"Telemetry retention sweep deleted {deleted} row(s) "
                        f"older than {days} day(s)"
                    )
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Telemetry retention sweep failed: {e}")
        await asyncio.sleep(interval)


def start_telemetry_retention_sweep() -> asyncio.Task:
    """Start the retention sweep task; call from the running event loop."""
    return asyncio.create_task(telemetry_retention_sweep_loop())


async def stop_telemetry_retention_sweep(task: asyncio.Task) -> None:
    """Cancel and await the sweep task; never raises."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Telemetry retention sweep stop error: {e}")
