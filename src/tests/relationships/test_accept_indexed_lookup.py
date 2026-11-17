"""The accept route resolves a pending request via one indexed lookup.

It used to fetch and convert every incoming request just to find the sender,
then discard all but one id. The direct (sender, recipient) lookup keeps the
same 404 behaviour and the manager's ownership/block re-validation.
"""

from typing import Any, cast

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.relationships.friend_requests import FriendRequestMixin


def _build(pending_row):
    relationships = MagicMock()
    relationships._get_pending_request.return_value = pending_row
    relationships.accept_friend_request.return_value = SimpleNamespace(
        updated_at=99, created_at=1
    )

    auth = MagicMock()
    auth.get_user_profiles_bulk.return_value = {}
    presence = MagicMock()
    presence.get_visible_presence.return_value = None

    handler = FriendRequestMixin()
    cast(Any, handler)._dispatch_relationship_event = AsyncMock()
    cast(Any, handler)._invalidate_relationship_list_cache = MagicMock()
    return handler, relationships


def _run(handler, relationships):
    current_user = SimpleNamespace(user_id=42)
    with (
        patch(
            "src.api.routes.relationships.friend_requests.api.get_relationships",
            return_value=relationships,
        ),
        patch(
            "src.api.routes.relationships.friend_requests.api.get_auth",
            return_value=MagicMock(get_user_profiles_bulk=lambda ids: {}),
        ),
        patch(
            "src.api.routes.relationships.friend_requests.api.get_presence",
            return_value=MagicMock(get_visible_presence=lambda *a: None),
        ),
    ):
        return asyncio.run(handler.accept_friend_request("5", current_user))


@pytest.mark.relationships
class TestAcceptIndexedLookup:
    def test_uses_pair_lookup_not_inbox_scan(self):
        handler, relationships = _build({"id": 77})

        result = _run(handler, relationships)

        relationships._get_pending_request.assert_called_once_with(5, 42)
        relationships.get_pending_requests_incoming.assert_not_called()
        relationships.accept_friend_request.assert_called_once_with(42, 77)
        assert result.success is True

    def test_missing_request_still_returns_404(self):
        handler, relationships = _build(None)

        with pytest.raises(HTTPException) as exc_info:
            _run(handler, relationships)

        assert exc_info.value.status_code == 404
        relationships.accept_friend_request.assert_not_called()
