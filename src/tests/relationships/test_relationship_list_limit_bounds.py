"""Relationship list reads clamp ``limit`` on both sides.

The list helpers previously did ``limit = max(0, limit)``. That stopped
SQLite's ``LIMIT -1`` ("unlimited") escape, but a huge positive value still
reached ``LIMIT ?`` unchanged and dumped a user's whole graph in one read --
including the private 256-character friend-request messages. Hold the line at
the shared choke point so every list method (and its legacy alias) stays
bounded regardless of which caller supplies the page size.
"""

import pytest

from src.core.relationships.manager.helpers import MAX_RELATIONSHIP_LIST_LIMIT

USER_ID = 42

LIST_METHODS = (
    "get_friends",
    "get_blocked_users",
    "get_pending_requests_incoming",
    "get_pending_requests_outgoing",
    "get_incoming_requests",
    "get_outgoing_requests",
)


@pytest.fixture
def captured_fetches(rel_manager, monkeypatch):
    """Capture the bound parameters of every ``fetch_all`` list read."""
    calls = []

    def fake_fetch_all(sql, params=()):
        calls.append((sql, tuple(params)))
        return []

    monkeypatch.setattr(rel_manager._db, "fetch_all", fake_fetch_all)
    return calls


@pytest.mark.relationships
class TestRelationshipListLimitBounds:
    @pytest.mark.parametrize("method", LIST_METHODS)
    def test_oversized_limit_is_capped(self, rel_manager, captured_fetches, method):
        getattr(rel_manager, method)(USER_ID, limit=10**12)
        assert len(captured_fetches) == 1
        sql, params = captured_fetches[0]
        assert "LIMIT ?" in sql
        assert params[-1] == MAX_RELATIONSHIP_LIST_LIMIT

    @pytest.mark.parametrize("method", LIST_METHODS)
    @pytest.mark.parametrize("raw, expected", [(-1, 0), (0, 0), (25, 25)])
    def test_small_and_negative_limits_are_preserved(
        self, rel_manager, captured_fetches, method, raw, expected
    ):
        getattr(rel_manager, method)(USER_ID, limit=raw)
        assert captured_fetches[0][1][-1] == expected

    @pytest.mark.parametrize("method", LIST_METHODS)
    def test_non_numeric_limit_falls_back_to_default(
        self, rel_manager, captured_fetches, method
    ):
        # Previously ``max(0, "oops")`` raised a TypeError that degraded to a
        # 500; the shared helper degrades to the documented default instead.
        getattr(rel_manager, method)(USER_ID, limit="oops")
        assert captured_fetches[0][1][-1] == 100
