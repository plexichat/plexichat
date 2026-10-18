"""Stash free-text search must treat LIKE metacharacters literally.

Mirrors the SUM workbook search contract in ``test_sum_search.py``: a literal
``%``, ``_`` or ``\\`` in the query must match only itself, not act as a
wildcard (docs/admin-hardening.md).
"""

from src.core.suite.stash import StashManager
from src.core.suite.stash.schema import create_tables as create_stash_tables


def _new_manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def test_list_items_search_treats_like_wildcards_literally(
    db, two_users, server_manager, media_manager
):
    owner, _other = two_users
    manager = _new_manager(db, server_manager, media_manager)

    for title in ("Alpha Report", "100% Coverage", "under_score", r"back\slash"):
        manager.create_item(
            owner.id, item_type="note", title=title, metadata={"content": "x"}
        )

    items, total = manager.list_items(owner.id, search="%")
    assert total == 1
    assert items[0]["title"] == "100% Coverage"

    items, total = manager.list_items(owner.id, search="_")
    assert total == 1
    assert items[0]["title"] == "under_score"

    items, total = manager.list_items(owner.id, search="\\")
    assert total == 1
    assert items[0]["title"] == r"back\slash"
