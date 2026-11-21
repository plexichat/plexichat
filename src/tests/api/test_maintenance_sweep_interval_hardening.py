"""Maintenance-sweep interval config must not fail open on non-finite values.

Each background sweep resolved its interval with ``max(float(raw), MIN)``.
Every comparison against NaN is False, so ``max(nan, MIN)`` returns *nan* and
``await asyncio.sleep(nan)`` raises ``ValueError``. Because that sleep sits
outside the loop's ``try/except``, the whole task dies on startup and the
cleanup it owns never runs again:

* poll expiry: expired polls are never marked ended (wrong AFTER_END results);
* telemetry retention: rows from the unauthenticated sink grow without bound;
* chunked-upload maintenance: abandoned sessions and their temp files are
  never reaped (temp-disk exhaustion).

An `inf` or a huge interval parks the sweep just as effectively. The boundary
must coerce such values to a finite, bounded interval instead.
"""

import asyncio as real_asyncio
import math
from types import SimpleNamespace

import pytest

from src.api import media_maintenance, poll_expiry, telemetry_maintenance


class _StopLoop(BaseException):
    """Break out of the infinite sweep loop once it reaches its sleep."""


_CASES = [
    pytest.param(
        poll_expiry,
        poll_expiry.poll_expiry_sweep_loop,
        "polls.expiry_sweep_interval_seconds",
        poll_expiry._SWEEP_MIN_INTERVAL_SEC,
        poll_expiry._SWEEP_DEFAULT_INTERVAL_SEC,
        id="poll-expiry",
    ),
    pytest.param(
        telemetry_maintenance,
        telemetry_maintenance.telemetry_retention_sweep_loop,
        "telemetry.retention_sweep_interval_seconds",
        telemetry_maintenance._MIN_SWEEP_INTERVAL_SEC,
        telemetry_maintenance._DEFAULT_SWEEP_INTERVAL_SEC,
        id="telemetry-retention",
    ),
    pytest.param(
        media_maintenance,
        media_maintenance.media_maintenance_sweep_loop,
        "media.chunked_upload.cleanup_interval",
        media_maintenance._SWEEP_MIN_INTERVAL_SEC,
        media_maintenance._SWEEP_DEFAULT_INTERVAL_SEC,
        id="media-maintenance",
    ),
]


def _run_and_capture(monkeypatch, module, loop_fn, interval_key, raw):
    """Drive one sweep tick and return the delay passed to sleep()."""
    captured = {}

    async def fake_sleep(delay):
        captured["delay"] = delay
        raise _StopLoop

    fake_asyncio = SimpleNamespace(
        sleep=fake_sleep,
        CancelledError=real_asyncio.CancelledError,
        get_running_loop=real_asyncio.get_running_loop,
    )
    monkeypatch.setattr(module, "asyncio", fake_asyncio)
    monkeypatch.setattr(
        module,
        "config",
        SimpleNamespace(
            get=lambda key, default=None: raw if key == interval_key else default
        ),
    )
    # Neutralise the sweep body so no DB / app-module work happens.
    if module is media_maintenance:

        async def no_op(loop):
            return None

        monkeypatch.setattr(module, "_sweep_once", no_op)
    else:
        core_name = "polls" if module is poll_expiry else "telemetry"
        core_module = __import__(f"src.core.{core_name}", fromlist=["is_setup"])
        monkeypatch.setattr(core_module, "is_setup", lambda: False)

    with pytest.raises(_StopLoop):
        real_asyncio.run(loop_fn())
    return captured["delay"]


@pytest.mark.parametrize("module,loop_fn,interval_key,minimum,default", _CASES)
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_interval_degrades_to_safe_default(
    monkeypatch, module, loop_fn, interval_key, minimum, default, bad
):
    delay = _run_and_capture(monkeypatch, module, loop_fn, interval_key, bad)
    assert math.isfinite(delay)
    assert minimum <= delay <= 86400.0
    assert delay == default


@pytest.mark.parametrize("module,loop_fn,interval_key,minimum,default", _CASES)
def test_huge_interval_is_capped(
    monkeypatch, module, loop_fn, interval_key, minimum, default
):
    delay = _run_and_capture(monkeypatch, module, loop_fn, interval_key, 10**12)
    assert math.isfinite(delay)
    assert delay == 86400.0


@pytest.mark.parametrize("module,loop_fn,interval_key,minimum,default", _CASES)
def test_legitimate_interval_is_respected(
    monkeypatch, module, loop_fn, interval_key, minimum, default
):
    wanted = minimum * 3
    delay = _run_and_capture(monkeypatch, module, loop_fn, interval_key, wanted)
    assert delay == wanted
