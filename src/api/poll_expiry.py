"""
Periodic poll-expiry sweep - Background task started from the app lifespan.

Persisted ``ended_at`` must not depend on someone reading or voting on a
poll: this bounded-interval sweep marks every expired poll as ended so
AFTER_END result visibility reflects reality at the DB level.
"""

import asyncio

import utils.config as config
import utils.logger as logger

from src.core.security.hardening import clamp_finite_float

_SWEEP_MIN_INTERVAL_SEC = 5.0
# A runaway interval (NaN/inf/huge) must never park the sweep forever: expired
# polls must always be reaped. Bound it at the config boundary.
_SWEEP_MAX_INTERVAL_SEC = 86400.0
_SWEEP_DEFAULT_INTERVAL_SEC = 60.0


async def poll_expiry_sweep_loop() -> None:
    from src.core import polls as polls_module

    # Resolve the serving loop once. ``asyncio.get_event_loop()`` used to run
    # on every sweep tick (a lookup + deprecation-prone path); the loop never
    # changes for the life of this task, so capture it once and reuse it for
    # every executor hand-off below.
    loop = asyncio.get_running_loop()
    # max(float(raw), MIN) fails open on NaN: max(nan, MIN) is nan and
    # asyncio.sleep(nan) raises, killing the task so expired polls go unreaped.
    interval = clamp_finite_float(
        config.get("polls.expiry_sweep_interval_seconds", _SWEEP_DEFAULT_INTERVAL_SEC),
        _SWEEP_MIN_INTERVAL_SEC,
        _SWEEP_MAX_INTERVAL_SEC,
        default=_SWEEP_DEFAULT_INTERVAL_SEC,
    )
    while True:
        try:
            if polls_module.is_setup():
                ended = await loop.run_in_executor(
                    None, polls_module.check_expired_polls
                )
                if ended:
                    logger.info(f"Poll expiry sweep ended {ended} poll(s)")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug(f"Poll expiry sweep failed: {e}")
        await asyncio.sleep(interval)


def start_poll_expiry_sweep() -> asyncio.Task:
    """Start the sweep task; call from the running event loop (lifespan)."""
    return asyncio.get_running_loop().create_task(poll_expiry_sweep_loop())


async def stop_poll_expiry_sweep(task: asyncio.Task) -> None:
    """Cancel and await the sweep task; never raises."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Poll expiry sweep stop error: {e}")
