"""Regression tests for keyset (seek) pagination on the stash item list.

BEFORE: ``list_items_page``'s "cursor" was a decimal OFFSET, so page N walked
and discarded N-1 rows and deep pages got progressively slower.

AFTER: an opt-in keyset path seeks via ``(pinned, updated_at, id) < ?`` with a
LIMIT+1 lookahead. These tests pin result parity with the legacy offset path,
the shared authorization filters, and the bounded (OFFSET-free) page query.
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import StashValidationError
from src.core.suite.stash.schema import create_tables as create_stash_tables
from src.core.suite.stash.stash_manager_items.listing import ItemKeysetCursor

TOTAL_ITEMS = 240
PAGE_LIMIT = 20


def _new_manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def _seed_items(
    db, manager, owner_id, count, *, base_updated=1000, prefix="Keyset note"
):
    ids = []
    for index in range(count):
        item = manager.create_item(
            owner_id,
            item_type="note",
            title=f"{prefix} {index:04d}",
            metadata={"content": "x"},
        )
        ids.append(str(item["id"]))
        # Distinct sort keys make the legacy OFFSET order deterministic, so
        # parity can be asserted as an exact ordered comparison rather than a
        # set comparison that would hide a reordering regression.
        db.execute(
            "UPDATE stash_items SET updated_at = ?, pinned = ? WHERE id = ?",
            (base_updated + index, 1 if index % 7 == 0 else 0, item["id"]),
        )
    return ids


class _RecordingDB:
    """Delegate to the real DB while recording statements and row counts."""

    def __init__(self, db):
        self._db = db
        self.queries = []

    def fetch_all(self, sql, params=()):
        rows = self._db.fetch_all(sql, params)
        self.queries.append((sql, tuple(params or ()), len(rows)))
        return rows

    def fetch_one(self, sql, params=()):
        self.queries.append((sql, tuple(params or ()), 1))
        return self._db.fetch_one(sql, params)

    def execute(self, sql, params=()):
        return self._db.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._db, name)


def _page_queries(recorded):
    return [
        entry
        for entry in recorded
        if "SELECT * FROM stash_items WHERE" in entry[0]
        and "ORDER BY pinned DESC" in entry[0]
    ]


def _walk_offset(manager, user_id, **filters):
    ids, total, offset = [], None, 0
    while True:
        page, total = manager.list_items(
            user_id, limit=PAGE_LIMIT, offset=offset, **filters
        )
        if not page:
            break
        ids.extend(str(item["id"]) for item in page)
        offset += PAGE_LIMIT
    return ids, total


def _walk_keyset(manager, user_id, **filters):
    ids, total, cursor, hops = [], None, None, 0
    while True:
        page, total, cursor = manager.list_items_keyset(
            user_id, limit=PAGE_LIMIT, cursor=cursor, **filters
        )
        ids.extend(str(item["id"]) for item in page)
        hops += 1
        assert hops <= TOTAL_ITEMS // PAGE_LIMIT + 2, "keyset chain did not terminate"
        if cursor is None:
            break
    return ids, total


def test_keyset_pages_match_legacy_offset_order(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    created = _seed_items(db, manager, owner.id, TOTAL_ITEMS)

    offset_ids, offset_total = _walk_offset(manager, owner.id)
    keyset_ids, keyset_total = _walk_keyset(manager, owner.id)

    assert offset_total == keyset_total == TOTAL_ITEMS
    assert keyset_ids == offset_ids
    assert len(keyset_ids) == len(set(keyset_ids)) == len(created)


def test_keyset_pages_preserve_filters_and_authorization(
    db, two_users, server_manager, media_manager
):
    owner, other = two_users
    manager = _new_manager(db, server_manager, media_manager)
    created = _seed_items(db, manager, owner.id, 60)
    granted = created[::3]
    for item_id in granted:
        manager.grant(owner.id, int(item_id), other.id, ["view_metadata"])
    own = _seed_items(db, manager, other.id, 7, base_updated=5000, prefix="Other item")

    visible = set(granted) | set(own)
    for filters in (
        {},
        {"pinned": True},
        {"search": "Keyset note"},
        {"scope": "personal"},
    ):
        offset_ids, offset_total = _walk_offset(manager, other.id, **filters)
        keyset_ids, keyset_total = _walk_keyset(manager, other.id, **filters)
        assert keyset_total == offset_total
        assert keyset_ids == offset_ids
        # The keyset path must not widen what the offset path already allows.
        assert set(keyset_ids) <= visible


def test_legacy_decimal_cursor_and_keyset_cursor_coexist(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    _seed_items(db, manager, owner.id, TOTAL_ITEMS)

    legacy_first, total, token = manager.list_items_page(owner.id, limit=PAGE_LIMIT)
    assert total == TOTAL_ITEMS
    assert token == str(PAGE_LIMIT)  # legacy cursor format unchanged
    legacy_second, _, token2 = manager.list_items_page(
        owner.id, limit=PAGE_LIMIT, cursor=token
    )
    assert token2 == str(2 * PAGE_LIMIT)
    assert [i["id"] for i in legacy_second] == [
        i["id"]
        for i in manager.list_items(owner.id, limit=PAGE_LIMIT, offset=PAGE_LIMIT)[0]
    ]

    keyset_first, keyset_total, keyset_token = manager.list_items_page(
        owner.id, limit=PAGE_LIMIT, keyset=True
    )
    assert keyset_total == TOTAL_ITEMS
    assert ItemKeysetCursor.is_keyset_token(keyset_token)
    assert [i["id"] for i in keyset_first] == [i["id"] for i in legacy_first]

    # A keyset token stays a keyset token through the legacy entry points.
    keyset_second, _, keyset_token2 = manager.list_items_page(
        owner.id, limit=PAGE_LIMIT, cursor=keyset_token
    )
    assert ItemKeysetCursor.is_keyset_token(keyset_token2)
    assert [i["id"] for i in keyset_second] == [i["id"] for i in legacy_second]
    via_legacy_api, _ = manager.list_items(
        owner.id, limit=PAGE_LIMIT, cursor=keyset_token
    )
    assert [i["id"] for i in via_legacy_api] == [i["id"] for i in keyset_second]


def test_deep_keyset_page_is_a_bounded_seek(
    db, two_users, server_manager, media_manager
):
    owner, _ = two_users
    manager = _new_manager(db, server_manager, media_manager)
    _seed_items(db, manager, owner.id, TOTAL_ITEMS)

    cursor = None
    first_cursor = None
    for hop in range(4):
        _, _, cursor = manager.list_items_keyset(
            owner.id, limit=PAGE_LIMIT, cursor=cursor
        )
        if hop == 0:
            first_cursor = cursor
    assert ItemKeysetCursor.is_keyset_token(cursor)

    recorder = _RecordingDB(db)
    manager._db = recorder
    deep_items, total, deep_next = manager.list_items_keyset(
        owner.id, limit=PAGE_LIMIT, cursor=cursor
    )
    assert total == TOTAL_ITEMS
    assert len(deep_items) == PAGE_LIMIT
    assert ItemKeysetCursor.is_keyset_token(deep_next)

    deep_queries = _page_queries(recorder.queries)
    assert len(deep_queries) == 1
    deep_sql, deep_params, deep_rows = deep_queries[0]
    assert "OFFSET" not in deep_sql.upper()
    assert "ORDER BY pinned DESC, updated_at DESC, id DESC LIMIT ?" in deep_sql
    assert "pinned < ?" in deep_sql
    assert "(updated_at < ? OR (updated_at = ? AND id < ?))" in deep_sql
    assert deep_rows <= PAGE_LIMIT + 1  # one-row lookahead, never a full scan
    assert deep_params[-1] == PAGE_LIMIT + 1

    anchor = ItemKeysetCursor.decode(cursor)
    assert anchor is not None
    assert list(deep_params[-6:-1]) == [
        anchor[0],
        anchor[0],
        anchor[1],
        anchor[1],
        anchor[2],
    ]

    # A shallow follow-up page issues the exact same statement; only the
    # cursor anchor values change, so cost does not grow with page depth.
    recorder.queries.clear()
    manager.list_items_keyset(owner.id, limit=PAGE_LIMIT, cursor=first_cursor)
    shallow_queries = _page_queries(recorder.queries)
    assert len(shallow_queries) == 1
    assert shallow_queries[0][0] == deep_sql


def test_keyset_cursor_roundtrip_and_malformed_tokens_fail_closed():
    token = ItemKeysetCursor.encode(1, 12345, 999)
    assert ItemKeysetCursor.decode(token) == (1, 12345, 999)
    assert ItemKeysetCursor.decode("100") is None  # legacy offset untouched
    assert ItemKeysetCursor.decode(None) is None
    with pytest.raises(StashValidationError):
        ItemKeysetCursor.decode("k1.not-base64!!")
    with pytest.raises(StashValidationError):
        ItemKeysetCursor.decode("k1." + "A" * 200)
