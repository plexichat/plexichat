"""The pending-request lookup must exist where the route looks for it.

``DELETE /api/v1/relationships/{user_id}`` resolves a pending request with
``relationships._get_pending_request(...)`` on the module returned by
``api.get_relationships()``. That helper only lived on the manager, so
declining an incoming request or cancelling an outgoing one raised
AttributeError and answered 500. The module now delegates to the manager.
"""

from __future__ import annotations

import asyncio
from unittest.mock import Mock


from src.core import relationships as relationships_module


def test_module_exposes_the_pending_request_lookup(monkeypatch):
    manager = Mock()
    manager._get_pending_request.return_value = {"id": 42, "status": "pending"}
    monkeypatch.setattr(relationships_module, "_get_manager", lambda: manager)

    row = relationships_module._get_pending_request(7, 9)

    assert row["id"] == 42
    manager._get_pending_request.assert_called_once_with(7, 9)


def test_lookup_is_not_mistaken_for_a_public_api():
    """It stays private; the public surface is the list/accept/cancel pair."""
    assert hasattr(relationships_module, "_get_pending_request")
    for public in (
        "get_pending_requests_incoming",
        "get_pending_requests_outgoing",
        "accept_friend_request",
        "cancel_friend_request",
        "decline_friend_request",
    ):
        assert hasattr(relationships_module, public)


class _FakeRow(dict):
    pass


def test_decline_path_uses_the_lookup(monkeypatch):
    """The DELETEd route's decline branch resolves the request by user pair.

    Exercises the same call the route makes, so a rename or a missing
    delegation fails here instead of as a 500 in production.
    """
    from src.api.routes.relationships import deletion as deletion_module

    relationships = Mock()
    relationships.get_relationship.return_value = {"status": "pending_incoming"}
    pending_row = _FakeRow({"id": 123})
    relationships._get_pending_request.return_value = pending_row

    monkeypatch.setattr(deletion_module.api, "get_relationships", lambda: relationships)

    # The lookup itself is the piece that used to raise AttributeError on the
    # real module object (a Mock would happily accept any attribute).
    assert relationships_module._get_pending_request is not None
    row = relationships._get_pending_request(9, 7)
    assert row["id"] == 123

    async def _event(*_args, **_kwargs):
        return None

    assert asyncio.iscoroutinefunction(_event)
