"""Regression tests for the bounded condition-wait helpers."""

from __future__ import annotations

import pytest

from src.tests._conftest.waiting import wait_until, wait_until_async


def test_returns_immediately_when_predicate_is_true():
    calls = []

    def predicate():
        calls.append(1)
        return True

    assert wait_until(predicate) is True
    assert len(calls) == 1


def test_returns_the_truthy_predicate_value():
    assert wait_until(lambda: "ready") == "ready"


def test_polls_until_truthy_then_returns():
    seen = []

    def predicate():
        seen.append(len(seen))
        return len(seen) >= 3

    assert wait_until(predicate, timeout=2.0, interval=0.001) is True
    assert len(seen) == 3


def test_times_out_with_caller_message():
    with pytest.raises(AssertionError, match="never became ready"):
        wait_until(
            lambda: False,
            timeout=0.05,
            interval=0.005,
            message="never became ready",
        )


def test_times_out_with_default_message():
    with pytest.raises(AssertionError):
        wait_until(lambda: False, timeout=0.02, interval=0.005)


def test_lazy_message_is_rendered_only_on_timeout():
    rendered = []

    def build_message():
        rendered.append(1)
        return "built lazily"

    with pytest.raises(AssertionError, match="built lazily"):
        wait_until(
            lambda: False,
            timeout=0.02,
            interval=0.005,
            message=build_message,
        )
    assert rendered == [1]


def test_lazy_message_is_not_rendered_on_success():
    rendered = []

    def build_message():
        rendered.append(1)
        return "built lazily"

    assert wait_until(lambda: True, message=build_message) is True
    assert rendered == []


async def _ready():
    return "ready"


@pytest.mark.asyncio
async def test_async_returns_the_truthy_predicate_value():
    assert await wait_until_async(lambda: "ready") == "ready"


@pytest.mark.asyncio
async def test_async_awaits_an_awaitable_predicate():
    assert await wait_until_async(_ready) == "ready"


@pytest.mark.asyncio
async def test_async_polls_until_truthy_then_returns():
    seen = []

    def predicate():
        seen.append(len(seen))
        return len(seen) >= 3

    assert await wait_until_async(predicate, timeout=2.0, interval=0.001) is True
    assert len(seen) == 3


@pytest.mark.asyncio
async def test_async_polls_an_async_predicate_until_truthy():
    calls = []

    async def predicate():
        calls.append(1)
        return len(calls) >= 2

    assert await wait_until_async(predicate, timeout=2.0, interval=0.001) is True
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_async_times_out_with_caller_message():
    with pytest.raises(AssertionError, match="never became ready"):
        await wait_until_async(
            lambda: False,
            timeout=0.05,
            interval=0.005,
            message="never became ready",
        )


@pytest.mark.asyncio
async def test_async_times_out_with_default_message():
    with pytest.raises(AssertionError):
        await wait_until_async(lambda: False, timeout=0.02, interval=0.005)


@pytest.mark.asyncio
async def test_async_lazy_message_is_rendered_only_on_timeout():
    rendered = []

    def build_message():
        rendered.append(1)
        return "built lazily"

    with pytest.raises(AssertionError, match="built lazily"):
        await wait_until_async(
            lambda: False,
            timeout=0.02,
            interval=0.005,
            message=build_message,
        )
    assert rendered == [1]
