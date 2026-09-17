"""Universal Suite search de-duplication tests.

``GET /suite/search`` fans out to a personal listing (which already includes
server-scoped rows visible to the caller) plus the shared/server listing, so
a server-scoped Stash/Sum row arrived twice as two identical cards. Results
must be deduped on launcher identity before bounding.
"""

import asyncio
import types

from src.api.routes.suite import registry as registry_routes


def test_dedupe_items_keeps_first_seen_order():
    items = [
        {"appId": "plexistash", "id": "7", "updatedAt": 3},
        {"appId": "plexisum", "id": "7", "updatedAt": 2},
        {"appId": "plexistash", "id": "7", "updatedAt": 3},
        {"appId": "plexistash", "id": "9", "updatedAt": 1},
    ]
    deduped = registry_routes._dedupe_items(items)
    assert deduped == [items[0], items[1], items[3]]
    # A different app with the same numeric id is a different launcher card.
    assert len({(i["appId"], i["id"]) for i in deduped}) == 3


def test_search_suite_dedupes_server_item_seen_in_both_feeds(monkeypatch):
    stash_row = {
        "id": 7,
        "title": "Server plan",
        "description": "shared",
        "pinned": False,
        "server_id": 11,
        "created_at": 100,
        "updated_at": 200,
    }
    projected = registry_routes._stash_home_item(stash_row)

    class FakeArtifactManager:
        def list_with_filters(self, filters=None, author_id=None, server_id=None):
            return []

    class FakeStashManager:
        def list_items(self, user_id, limit=50, search=None, **kwargs):
            return [dict(stash_row)], 1

    class FakeSumManager:
        def list_workbooks(self, user_id, limit=50, offset=0, search=None, **kwargs):
            return [], 0

    monkeypatch.setattr(registry_routes, "_get_manager", lambda: FakeArtifactManager())
    monkeypatch.setattr(
        registry_routes,
        "_suite_managers",
        lambda: (FakeStashManager(), FakeSumManager()),
    )
    monkeypatch.setattr(registry_routes, "_domain_available", lambda capability: True)
    # The shared feed re-reports the same server-scoped row the personal
    # listing already returned (personal scope includes visible server rows).
    monkeypatch.setattr(
        registry_routes,
        "_shared_items",
        lambda user_id, limit, search=None: [dict(projected)],
    )

    result = asyncio.run(
        registry_routes.search_suite(
            q="plan",
            limit=24,
            current_user=types.SimpleNamespace(user_id=42),
        )
    )
    matching = [
        item
        for item in result["items"]
        if (item["appId"], item["id"]) == ("plexistash", "7")
    ]
    assert len(matching) == 1
    assert result["total"] == len(result["items"])
