"""Regression tests for M2: friend-request spam rate limit + privacy flag.

Covers ``POST /friends/requests`` (``FriendRequestMixin.create_relationship``):
- Burst: more than ``FRIEND_REQUEST_RATE_LIMIT`` sends in one window from the
  same sender is rejected with 429.
- Privacy: a recipient with ``allow_friend_requests`` disabled gets a 403
  denial; the default (flag unset) still allows the request.
"""

import asyncio
import time
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.relationships import friend_requests as fr_route
from src.api.routes.relationships.friend_requests import (
    FRIEND_REQUEST_RATE_LIMIT,
    FriendRequestMixin,
    _check_friend_request_rate_limit,
)
from src.api.schemas.relationships import FriendRequestCreate


def _unique_sender() -> int:
    # Unique per test so the shared in-memory rate-limit counters never
    # collide with other tests running in the same process.
    return int(time.time() * 1000) % 1_000_000_000 + hash(uuid.uuid4().hex) % 1_000_000


def _run_route(handler, body, current_user):
    return asyncio.run(handler.create_relationship(body, current_user))


def _patched_route(send_return=None, settings_value=None):
    """Build a mixin handler with relationships/auth/settings mocked."""
    relationships = MagicMock()
    if send_return is not None:
        relationships.send_friend_request.return_value = send_return

    settings = MagicMock()
    settings.get.return_value = settings_value

    handler = FriendRequestMixin()
    handler._dispatch_relationship_event = AsyncMock()
    handler._invalidate_relationship_list_cache = MagicMock()

    patches = (
        patch(
            "src.api.routes.relationships.friend_requests.api.get_relationships",
            return_value=relationships,
        ),
        patch(
            "src.api.routes.relationships.friend_requests.api.get_auth",
            return_value=None,
        ),
        patch(
            "src.api.routes.relationships.friend_requests.api.get_settings",
            return_value=settings,
        ),
    )
    return handler, relationships, settings, patches


def _pending_request(target_id: int):
    return SimpleNamespace(status="pending", created_at=1234567890)


@pytest.mark.relationships
class TestFriendRequestRateLimitM2:
    """M2: per-sender burst over the limit is rejected with 429."""

    def test_helper_allows_budget_then_rejects_burst(self):
        sender = _unique_sender()
        for _ in range(FRIEND_REQUEST_RATE_LIMIT):
            _check_friend_request_rate_limit(sender)  # must not raise
        with pytest.raises(HTTPException) as exc_info:
            _check_friend_request_rate_limit(sender)
        assert exc_info.value.status_code == 429

    def test_route_burst_over_limit_rejected(self):
        sender = _unique_sender()
        current_user = SimpleNamespace(user_id=sender)

        handler, relationships, _, patches = _patched_route()
        # Each send returns a fresh pending request so the route succeeds
        # until the rate limiter trips (distinct recipients avoid dupes).
        relationships.send_friend_request.side_effect = (
            lambda sender_id, recipient_id, message=None: _pending_request(recipient_id)
        )
        with patches[0], patches[1], patches[2]:
            for i in range(FRIEND_REQUEST_RATE_LIMIT):
                body = FriendRequestCreate(user_id=900000000 + i)
                resp = _run_route(handler, body, current_user)
                assert resp.status == "pending_outgoing"

            with pytest.raises(HTTPException) as exc_info:
                _run_route(
                    handler,
                    FriendRequestCreate(user_id=900099999),
                    current_user,
                )
        assert exc_info.value.status_code == 429


@pytest.mark.relationships
class TestFriendRequestPrivacyFlagM2:
    """M2: recipient's allow_friend_requests flag is honoured."""

    def test_recipient_disabled_rejects_with_403(self):
        current_user = SimpleNamespace(user_id=_unique_sender())
        handler, relationships, _, patches = _patched_route(settings_value="false")
        with (
            patches[0],
            patches[1],
            patches[2],
            patch.object(
                fr_route, "_check_friend_request_rate_limit", return_value=None
            ),
        ):
            with pytest.raises(HTTPException) as exc_info:
                _run_route(
                    handler,
                    FriendRequestCreate(user_id=910000001),
                    current_user,
                )
        assert exc_info.value.status_code == 403
        # The privacy denial must not leak anything about blocks.
        detail = str(exc_info.value.detail).lower()
        assert "block" not in detail
        relationships.send_friend_request.assert_not_called()

    def test_recipient_default_unset_allows_request(self):
        current_user = SimpleNamespace(user_id=_unique_sender())
        handler, relationships, _, patches = _patched_route(settings_value=None)
        relationships.send_friend_request.return_value = _pending_request(910000002)
        with (
            patches[0],
            patches[1],
            patches[2],
            patch.object(
                fr_route, "_check_friend_request_rate_limit", return_value=None
            ),
        ):
            resp = _run_route(
                handler, FriendRequestCreate(user_id=910000002), current_user
            )
        assert resp.status == "pending_outgoing"
        relationships.send_friend_request.assert_called_once()
