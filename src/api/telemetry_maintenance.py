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

from src.core.security.hardening import clamp_finite_float

_MIN_SWEEP_INTERVAL_SEC = 60.0
# NaN/inf/huge intervals must not park the retention sweep: telemetry rows
# from the unauthenticated sink rely on this task for bounded growth.
_MAX_SWEEP_INTERVAL_SEC = 86400.0
_DEFAULT_SWEEP_INTERVAL_SEC = 3600.0


async def telemetry_retention_sweep_loop() -> None:
    from src.core import telemetry as telemetry_module

    # Resolve the serving loop once; the loop is fixed for the task's life, so
    # each scheduled sweep reuses it instead of re-running get_event_loop().
    loop = asyncio.get_running_loop()
    # max(float(raw), MIN) fails open on NaN: max(nan, MIN) is nan and
    # asyncio.sleep(nan) raises, killing the task so telemetry grows unbounded.
    interval = clamp_finite_float(
        config.get(
            "telemetry.retention_sweep_interval_seconds", _DEFAULT_SWEEP_INTERVAL_SEC
        ),
        _MIN_SWEEP_INTERVAL_SEC,
        _MAX_SWEEP_INTERVAL_SEC,
        default=_DEFAULT_SWEEP_INTERVAL_SEC,
    )
    while True:
        try:
            if telemetry_module.is_setup():
                days = int(config.get("telemetry.retention_days", 30))
                deleted = await loop.run_in_executor(
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
    return asyncio.get_running_loop().create_task(telemetry_retention_sweep_loop())


async def stop_telemetry_retention_sweep(task: asyncio.Task) -> None:
    """Cancel and await the sweep task; never raises."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Telemetry retention sweep stop error: {e}")
