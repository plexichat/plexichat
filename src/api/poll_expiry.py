"""
Periodic poll-expiry sweep - Background task started from the app lifespan.

Persisted ``ended_at`` must not depend on someone reading or voting on a
poll: this bounded-interval sweep marks every expired poll as ended so
AFTER_END result visibility reflects reality at the DB level.
"""

import asyncio

import utils.config as config
import utils.logger as logger

_SWEEP_MIN_INTERVAL_SEC = 5.0


async def poll_expiry_sweep_loop() -> None:
    from src.core import polls as polls_module

    interval = float(config.get("polls.expiry_sweep_interval_seconds", 60))
    interval = max(interval, _SWEEP_MIN_INTERVAL_SEC)
    while True:
        try:
            if polls_module.is_setup():
                ended = await asyncio.get_event_loop().run_in_executor(
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
    return asyncio.create_task(poll_expiry_sweep_loop())


async def stop_poll_expiry_sweep(task: asyncio.Task) -> None:
    """Cancel and await the sweep task; never raises."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.debug(f"Poll expiry sweep stop error: {e}")
