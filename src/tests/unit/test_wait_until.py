"""Regression tests for the bounded condition-wait helper."""

from __future__ import annotations

import pytest

from src.tests._conftest.waiting import wait_until


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
