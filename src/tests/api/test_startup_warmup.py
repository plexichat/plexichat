"""Regression tests for the boot-time module warm-up.

The warm-up exists only to move a one-off cold-start cost off the first
request. That means its failure modes are the interesting part: it must
never raise into startup, must honour its config gate, and must stop with
the rest of the lifespan tasks.
"""

import asyncio

import pytest

import src.api as api
from src.api import warmup


def test_warm_once_never_raises_and_reports_elapsed():
    elapsed = warmup.warm_once()
    assert elapsed >= 0.0


def test_warm_once_survives_a_broken_module(monkeypatch):
    def boom():
        raise RuntimeError("module init exploded")

    monkeypatch.setattr(api, "get_servers", boom)
    # A broken module must be skipped, not propagated into startup.
    elapsed = warmup.warm_once()
    assert elapsed >= 0.0


def test_warm_once_ignores_missing_getters(monkeypatch):
    for name in warmup._WARM_GETTERS:
        monkeypatch.delattr(api, f"get_{name}", raising=False)
    assert warmup.warm_once() >= 0.0


def test_enabled_defaults_to_true_without_config():
    assert warmup._warmup_enabled() is True


def test_delay_is_clamped_and_parsed(monkeypatch):
    monkeypatch.setattr(warmup.config, "get", lambda key, default=None: -5)
    assert warmup._warmup_delay() == 0.0

    monkeypatch.setattr(warmup.config, "get", lambda key, default=None: 10_000)
    assert warmup._warmup_delay() == warmup._MAX_DELAY_SECONDS

    monkeypatch.setattr(warmup.config, "get", lambda key, default=None: "not-a-number")
    assert warmup._warmup_delay() == 2.0


def test_disabled_warmup_does_nothing(monkeypatch):
    monkeypatch.setattr(warmup, "_warmup_enabled", lambda: False)
    called = []
    monkeypatch.setattr(warmup, "warm_once", lambda: called.append(True) or 0.0)

    asyncio.run(warmup.warmup_loop())

    assert called == []


def test_warmup_runs_once_when_enabled(monkeypatch):
    monkeypatch.setattr(warmup, "_warmup_enabled", lambda: True)
    monkeypatch.setattr(warmup, "_warmup_delay", lambda: 0.0)
    called = []
    monkeypatch.setattr(warmup, "warm_once", lambda: called.append(True) or 1.5)

    asyncio.run(warmup.warmup_loop())

    assert called == [True]


def test_stop_warmup_cancels_the_task():
    async def scenario():
        task = asyncio.create_task(asyncio.sleep(60))
        await warmup.stop_warmup(task)
        return task

    task = asyncio.run(scenario())
    assert task.cancelled()


def test_stop_warmup_tolerates_none():
    asyncio.run(warmup.stop_warmup(None))


@pytest.mark.parametrize("name", [n for n in warmup._WARM_GETTERS])
def test_warm_getters_exist_on_the_api_module(name):
    assert callable(getattr(api, f"get_{name}", None))
