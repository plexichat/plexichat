"""Fail-closed friend-request rate limiter regression tests.

``_check_friend_request_rate_limit`` guards a route that delivers a message to
another user. An unenforced limit is a harassment/spam primitive, so any path
that cannot establish or spend the shared counter must deny with 503 instead
of silently allowing the send. The counter must also be spent atomically: a
separate peek-then-increment let concurrent requests all read the same
sub-limit value and then push the counter past the budget.
"""

import asyncio
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.relationships.friend_requests import (
    FRIEND_REQUEST_RATE_LIMIT,
    _check_friend_request_rate_limit,
)


def _unique_sender() -> int:
    return int(time.time() * 1000) % 1_000_000_000 + hash(uuid.uuid4().hex) % 1_000_000


def _call(user_id: int) -> None:
    asyncio.run(_check_friend_request_rate_limit(user_id))


@pytest.mark.relationships
class TestFriendRequestLimiterFailClosed:
    def test_setup_failure_denies_with_503(self):
        with (
            patch(
                "src.core.ratelimit.is_setup", side_effect=RuntimeError("store down")
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                _call(_unique_sender())
        assert exc.value.status_code == 503

    def test_missing_manager_denies_with_503(self):
        with (
            patch("src.core.ratelimit.is_setup", return_value=True),
            patch(
                "src.core.ratelimit.get_manager",
                side_effect=RuntimeError("manager unavailable"),
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                _call(_unique_sender())
        assert exc.value.status_code == 503

    def test_counter_spend_failure_denies_with_503(self):
        manager = SimpleNamespace(
            aincrement_custom_usage=AsyncMock(side_effect=RuntimeError("write failed"))
        )
        with (
            patch("src.core.ratelimit.is_setup", return_value=True),
            patch("src.core.ratelimit.get_manager", return_value=manager),
        ):
            with pytest.raises(HTTPException) as exc:
                _call(_unique_sender())
        assert exc.value.status_code == 503

    def test_budget_is_spent_with_one_atomic_increment(self):
        manager = SimpleNamespace(aincrement_custom_usage=AsyncMock(return_value=1))
        with (
            patch("src.core.ratelimit.is_setup", return_value=True),
            patch("src.core.ratelimit.get_manager", return_value=manager),
        ):
            _call(_unique_sender())
        # A single atomic spend (amount=1) replaces the old peek (amount=0)
        # plus a separate increment, closing the concurrent check/increment race.
        manager.aincrement_custom_usage.assert_awaited_once()
        assert manager.aincrement_custom_usage.await_args.args[2] == 1

    def test_burst_boundary_rejects_over_budget(self):
        values = iter(range(1, FRIEND_REQUEST_RATE_LIMIT + 2))
        manager = SimpleNamespace(
            aincrement_custom_usage=AsyncMock(side_effect=lambda *a, **k: next(values))
        )
        with (
            patch("src.core.ratelimit.is_setup", return_value=True),
            patch("src.core.ratelimit.get_manager", return_value=manager),
        ):
            for _ in range(FRIEND_REQUEST_RATE_LIMIT):
                _call(_unique_sender())
            with pytest.raises(HTTPException) as exc:
                _call(_unique_sender())
        assert exc.value.status_code == 429
