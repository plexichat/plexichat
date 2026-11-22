"""Regression tests for batched grant resolution in PlexiSum workbook listing.

``SumManagerWorkbooks.list_workbooks`` used to resolve every visible workbook's
viewer permissions independently. Each non-owned row issued its own
``sum_grants`` read, so a page of N shared workbooks cost N grant queries.

These tests pin three properties of the batched replacement:

* parity -- permissions reported by the listing match the single-workbook
  authoritative path (``_viewer_permissions`` / ``get_workbook``);
* bounds -- one grant query for a whole page, including the max-size page and
  paginated windows, with no query at all for an empty page;
* isolation -- the batch is keyed to the requesting viewer, so another
  user's grants never appear in the response and expired/revoked grants stay
  excluded (fail-closed).
"""

from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables


class _GrantQuerySpy:
    """Count reads of the grant table while delegating everything else."""

    def __init__(self, db):
        self._db = db
        self.grant_queries = 0
        self.grant_params = []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def fetch_all(self, query, params=None):
        # The listing's own SELECT uses ``FROM sum_grants g`` inside an EXISTS
        # subquery; only the standalone grant lookups use ``FROM sum_grants
        # WHERE``, so count those and only those.
        if "FROM sum_grants WHERE" in query:
            self.grant_queries += 1
            self.grant_params.append(tuple(params or ()))
        return self._db.fetch_all(query, params)


def _make_manager(db, server_manager):
    create_tables(db)
    return SumManager(db, servers_module=server_manager)


def _shared(db, manager, owner_id, viewer_id, count, permission="view"):
    """Create ``count`` personal workbooks owned by ``owner_id`` and share them."""
    ids = []
    for index in range(count):
        workbook = manager.create_workbook(owner_id, f"Shared {index}")
        wb_id = int(workbook["id"])
        manager.grant_workbook(owner_id, wb_id, viewer_id, [permission])
        ids.append(wb_id)
    return ids


def test_list_workbooks_batches_grant_queries(db, two_users, server_manager):
    """A page of shared workbooks costs one grant read, not one per row."""
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    _shared(db, manager, owner.id, viewer.id, 12, "view")
    _shared(db, manager, owner.id, viewer.id, 4, "edit")
    owned = [
        int(manager.create_workbook(viewer.id, f"Mine {i}")["id"]) for i in range(3)
    ]

    # Authoritative per-workbook permissions, captured before spying so the
    # parity comparison is independent of the batched path under test.
    expected = {
        str(wb_id): manager._viewer_permissions(viewer.id, wb_id)
        for wb_id in (
            [int(w["id"]) for w in manager.list_workbooks(viewer.id, limit=100)[0]]
        )
    }
    assert len(expected) == 19

    real_db = manager._db
    spy = _GrantQuerySpy(real_db)
    manager._db = spy
    try:
        items, total = manager.list_workbooks(viewer.id, limit=100)
    finally:
        manager._db = real_db

    assert total == 19
    assert len(items) == 19
    assert spy.grant_queries == 1, (
        "listing issued %d grant queries for 19 workbooks; expected 1"
        % spy.grant_queries
    )
    # Parity: every row's reported access matches the single-workbook path.
    for item in items:
        assert item["my_permissions"] == expected[item["id"]], item["id"]
    assert all(str(wb_id) in expected for wb_id in owned)


def test_list_workbooks_page_bounds_keep_one_grant_query(db, two_users, server_manager):
    """The 100-row cap and OFFSET windows still use a single grant read."""
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    _shared(db, manager, owner.id, viewer.id, 105, "view")

    real_db = manager._db
    for limit, offset, expected_rows in (
        (100, 0, 100),
        (100, 100, 5),
        (10_000, 0, 100),  # direct callers cannot widen the page beyond 100
    ):
        spy = _GrantQuerySpy(real_db)
        manager._db = spy
        try:
            items, total = manager.list_workbooks(viewer.id, limit=limit, offset=offset)
        finally:
            manager._db = real_db
        assert total == 105
        assert len(items) == expected_rows
        assert spy.grant_queries == 1, (
            "page(limit=%s, offset=%s) issued %d grant queries"
            % (limit, offset, spy.grant_queries)
        )

    # An empty page performs no grant read at all (no IN () query).
    spy = _GrantQuerySpy(real_db)
    manager._db = spy
    try:
        items, total = manager.list_workbooks(viewer.id, limit=10, offset=10_000)
    finally:
        manager._db = real_db
    assert items == []
    assert total == 105
    assert spy.grant_queries == 0


def test_list_workbooks_permissions_never_cross_users(db, three_users, server_manager):
    """A viewer only ever receives their own effective access, never another's."""
    owner, viewer, stranger = three_users
    manager = _make_manager(db, server_manager)
    shared = manager.create_workbook(owner.id, "Shared with viewer")
    shared_id = int(shared["id"])
    manager.grant_workbook(owner.id, shared_id, viewer.id, ["view"])
    manager.grant_workbook(owner.id, shared_id, stranger.id, ["edit"])

    other = manager.create_workbook(owner.id, "Stranger only")
    other_id = int(other["id"])
    manager.grant_workbook(owner.id, other_id, stranger.id, ["edit"])

    items, _ = manager.list_workbooks(viewer.id, limit=100)
    by_id = {i["id"]: i for i in items}
    assert str(shared_id) in by_id
    assert str(other_id) not in by_id  # not visible to viewer
    perms = by_id[str(shared_id)]["my_permissions"]
    assert perms["can_view"] is True
    assert perms["can_edit"] is False  # stranger's edit grant must not leak
    assert perms["reason"] == "view_only"

    stranger_items, _ = manager.list_workbooks(stranger.id, limit=100)
    stranger_by_id = {i["id"]: i for i in stranger_items}
    assert stranger_by_id[str(other_id)]["my_permissions"]["can_edit"] is True
    assert stranger_by_id[str(shared_id)]["my_permissions"]["can_edit"] is True


def test_bulk_grant_lookup_is_per_user_expiry_and_revocation_aware(
    db, three_users, server_manager
):
    """The batch helper uses the same filters as the per-workbook path."""
    import time

    owner, viewer, stranger = three_users
    manager = _make_manager(db, server_manager)
    wb = manager.create_workbook(owner.id, "Bulk")
    wb_id = int(wb["id"])
    manager.grant_workbook(owner.id, wb_id, viewer.id, ["view"])
    manager.grant_workbook(owner.id, wb_id, stranger.id, ["edit"])
    expired = manager.create_workbook(owner.id, "Expired")
    expired_id = int(expired["id"])
    # grant_workbook rejects past expiries, so insert the already-expired row
    # directly to exercise the expiry filter in the batched lookup.
    now_ms = int(time.time() * 1000)
    manager._db.execute(
        "INSERT INTO sum_grants (id, workbook_id, subject_id, permissions, "
        "expires_at, revoked_at, created_by, created_at) VALUES (?, ?, ?, ?, ?, NULL, ?, ?)",
        (
            manager._generate_id(),
            expired_id,
            viewer.id,
            manager._json(["edit"]),
            now_ms - 1000,
            owner.id,
            now_ms,
        ),
    )
    revoked = manager.create_workbook(owner.id, "Revoked")
    revoked_id = int(revoked["id"])
    grant = manager.grant_workbook(owner.id, revoked_id, viewer.id, ["edit"])
    manager.revoke_grant(owner.id, int(grant["id"]))

    lookup = manager._grant_permissions_bulk([wb_id, expired_id, revoked_id], viewer.id)
    assert lookup.get(wb_id) == frozenset({"view"})
    assert lookup.get(expired_id) in (None, frozenset())
    assert lookup.get(revoked_id) in (None, frozenset())
    assert manager._grant_permissions_bulk([wb_id], stranger.id).get(
        wb_id
    ) == frozenset({"edit"})
    assert manager._grant_permissions_bulk([], viewer.id) == {}
