"""PlexiSum workbook listing search tests.

Covers the server-side title search end-to-end: the manager filters by a
case-insensitive title substring, and the REST route forwards the ``search``
query parameter (previously dead code — the manager accepted ``search`` but
no route or client ever sent it).
"""

import asyncio
import types

from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _make_manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


# ── manager search ────────────────────────────────────────────────────────


def test_list_workbooks_search_matches_title_case_insensitively(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    manager.create_workbook(owner.id, "Alpha Report")
    manager.create_workbook(owner.id, "Beta Notes")
    manager.create_workbook(owner.id, "Quarterly Report")

    items, total = manager.list_workbooks(owner.id, search="report")
    assert total == 2
    assert {w["title"] for w in items} == {"Alpha Report", "Quarterly Report"}

    # Search is case-insensitive (the manager lowercases both sides).
    items, total = manager.list_workbooks(owner.id, search="REPORT")
    assert total == 2

    items, total = manager.list_workbooks(owner.id, search="note")
    assert total == 1
    assert items[0]["title"] == "Beta Notes"

    # A partial substring matches anywhere in the title.
    items, total = manager.list_workbooks(owner.id, search="quarter")
    assert total == 1
    assert items[0]["title"] == "Quarterly Report"


def test_list_workbooks_search_is_scoped_to_visible_workbooks(
    db, two_users, server_manager
):
    owner, other = two_users
    manager = _make_manager(db, server_manager)
    manager.create_workbook(owner.id, "Private Report")
    shared = manager.create_workbook(owner.id, "Shared Report")
    manager.create_workbook(owner.id, "Shared Notes")
    manager.grant_workbook(owner.id, int(shared["id"]), other.id, ["view"])

    # The other user only sees workbooks granted to them, and search stays
    # within that visibility boundary.
    items, total = manager.list_workbooks(other.id, search="report")
    assert total == 1
    assert items[0]["title"] == "Shared Report"

    items, total = manager.list_workbooks(other.id, search="private")
    assert total == 0


def test_list_workbooks_search_excludes_templates_in_regular_listing(
    db, two_users, server_manager
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    manager.create_workbook(owner.id, "Budget template", template=True)
    manager.create_workbook(owner.id, "Budget 2026")

    items, total = manager.list_workbooks(owner.id, search="budget")
    assert total == 1
    assert items[0]["title"] == "Budget 2026"

    # Templates are searchable within the template listing.
    items, total = manager.list_workbooks(owner.id, search="budget", template=True)
    assert total == 1
    assert items[0]["title"] == "Budget template"


# ── route wiring ──────────────────────────────────────────────────────────


def _assert_route_forwards_search(monkeypatch, search, expected):
    from src.api.routes.suite import sum as sum_routes

    captured = {}

    class FakeManager:
        def list_workbooks(self, user_id, limit, offset, template=False, search=None):
            captured["user_id"] = user_id
            captured["search"] = search
            return [], 0

    # Call the route handler directly; ``_manager`` is resolved lazily and
    # ``current_user`` only needs ``user_id`` here.
    monkeypatch.setattr(sum_routes, "_manager", lambda: FakeManager())
    asyncio.run(
        sum_routes.list_workbooks(
            limit=10,
            offset=0,
            template=False,
            search=search,
            current_user=types.SimpleNamespace(user_id=42),
        )
    )
    assert captured["user_id"] == 42
    assert captured["search"] == expected


def test_list_workbooks_route_forwards_search(monkeypatch):
    _assert_route_forwards_search(monkeypatch, "budget", "budget")


def test_list_workbooks_route_normalizes_blank_search(monkeypatch):
    _assert_route_forwards_search(monkeypatch, "", None)
    _assert_route_forwards_search(monkeypatch, "   ", None)
