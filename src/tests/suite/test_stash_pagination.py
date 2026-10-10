"""Regression tests for M5: stash listing must not silently truncate at 500.

Covers cursor/limit pagination on the item listing (``list_items_page``):
page 1 returns ``limit`` items plus a ``next_cursor``, following the cursor
chain returns every remaining item, and the legacy ``list_items``
offset listing stays backward compatible (2-tuple with total count).
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import StashValidationError
from src.core.suite.stash.schema import create_tables as create_stash_tables

TOTAL_ITEMS = 600
PAGE_LIMIT = 100


def _new_manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def _create_many(manager, owner_id, count):
    ids = []
    for index in range(count):
        item = manager.create_item(
            owner_id,
            item_type="note",
            title=f"Paginated note {index:04d}",
            metadata={"content": "x"},
        )
        ids.append(str(item["id"]))
    return ids


def _walk_cursor_pages(manager, user_id, limit, **filters):
    seen = []
    cursor = None
    total = None
    hops = 0
    while True:
        items, page_total, next_cursor = manager.list_items_page(
            user_id, limit=limit, cursor=cursor, **filters
        )
        if total is None:
            total = page_total
        else:
            assert page_total == total
        seen.extend(str(item["id"]) for item in items)
        hops += 1
        assert hops <= (TOTAL_ITEMS // limit) + 2, "cursor chain did not terminate"
        if next_cursor is None:
            break
        cursor = next_cursor
    return seen, total


def test_cursor_pagination_covers_600_items_no_truncation(
    db, two_users, server_manager, media_manager
):
    """M5 repro: 600 items must all be reachable; none hidden past 500."""
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    created = _create_many(manager, owner.id, TOTAL_ITEMS)

    first, total, next_cursor = manager.list_items_page(owner.id, limit=PAGE_LIMIT)
    assert total == TOTAL_ITEMS
    assert len(first) == PAGE_LIMIT
    assert next_cursor is not None

    seen, walked_total = _walk_cursor_pages(manager, owner.id, PAGE_LIMIT)
    assert walked_total == TOTAL_ITEMS
    assert len(seen) == TOTAL_ITEMS
    assert sorted(seen) == sorted(created)
    assert len(set(seen)) == TOTAL_ITEMS


def test_granted_items_beyond_500_visible_via_cursor(
    db, two_users, server_manager, media_manager
):
    """Grantee visibility must survive >500 grant rows (internal LIMIT 500)."""
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    created = _create_many(manager, owner.id, TOTAL_ITEMS)
    for item_id in created:
        manager.grant(owner.id, item_id, other.id, ["view_metadata"])

    seen, total = _walk_cursor_pages(manager, other.id, PAGE_LIMIT)
    assert total == TOTAL_ITEMS
    assert sorted(seen) == sorted(created)


def test_offset_listing_stays_backward_compatible(
    db, two_users, server_manager, media_manager
):
    """Legacy offset listing keeps its 2-tuple shape, total, and full reach."""
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    created = _create_many(manager, owner.id, TOTAL_ITEMS)

    items, total = manager.list_items(owner.id)
    assert total == TOTAL_ITEMS
    assert len(items) == 50  # default limit preserved

    seen = []
    for offset in range(0, TOTAL_ITEMS, PAGE_LIMIT):
        page, page_total = manager.list_items(owner.id, limit=PAGE_LIMIT, offset=offset)
        assert page_total == TOTAL_ITEMS
        seen.extend(str(item["id"]) for item in page)
    assert sorted(seen) == sorted(created)

    # An explicit cursor overrides the offset.
    via_cursor, _ = manager.list_items(owner.id, limit=5, offset=0, cursor="100")
    via_offset, _ = manager.list_items(owner.id, limit=5, offset=100)
    assert [i["id"] for i in via_cursor] == [i["id"] for i in via_offset]


def test_limit_clamped_and_bad_cursor_rejected(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    _create_many(manager, owner.id, 10)

    items, total, next_cursor = manager.list_items_page(owner.id, limit=5000)
    assert len(items) == 10  # only 10 exist; clamp keeps query bounded
    assert total == 10
    assert next_cursor is None

    items, _, next_cursor = manager.list_items_page(owner.id, limit=5)
    assert len(items) == 5
    assert next_cursor is not None  # 5 of 10 -> more rows exist

    with pytest.raises(StashValidationError):
        manager.list_items_page(owner.id, cursor="not-a-cursor")
    with pytest.raises(StashValidationError):
        manager.list_items_page(owner.id, cursor="-3")
    with pytest.raises(StashValidationError):
        manager.list_items(owner.id, cursor="bogus")
