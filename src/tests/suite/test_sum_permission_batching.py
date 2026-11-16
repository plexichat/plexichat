"""Regression tests for batched PlexiSum grant resolution.

Authorization and viewer-permission projection used to issue two or three
``_has_grant`` queries (each with a fresh ``now``, defeating the DB query
cache) per check. They now resolve a workbook's live grants once per check.
These tests pin the resulting query count and the unchanged access semantics.
"""

from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables


class _GrantQuerySpy:
    """Count grant-table reads while delegating everything else to the DB."""

    def __init__(self, db):
        self._db = db
        self.grant_queries = 0

    def __getattr__(self, name):
        return getattr(self._db, name)

    def fetch_all(self, query, params=None):
        if "FROM sum_grants" in query:
            self.grant_queries += 1
        return self._db.fetch_all(query, params)


def _make_manager(db, server_manager):
    create_tables(db)
    return SumManager(db, servers_module=server_manager)


def test_get_workbook_issues_one_grant_query_per_check(db, two_users, server_manager):
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Shared")
    wb_id = int(workbook["id"])
    manager.grant_workbook(owner.id, wb_id, viewer.id, ["view"])

    spy = _GrantQuerySpy(db)
    manager._db = spy
    result = manager.get_workbook(viewer.id, wb_id)

    # One grant read in _workbook/_authorize and one in _viewer_permissions.
    # The old path needed five (two for edit/view authorization, three for the
    # viewer-permission projection).
    assert spy.grant_queries == 2
    assert result["my_permissions"]["can_view"] is True
    assert result["my_permissions"]["can_edit"] is False
    assert result["my_permissions"]["reason"] == "view_only"


def test_owner_path_does_not_read_grants(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Owned")
    wb_id = int(workbook["id"])

    spy = _GrantQuerySpy(db)
    manager._db = spy
    result = manager.get_workbook(owner.id, wb_id)

    assert spy.grant_queries == 0
    assert result["my_permissions"]["can_edit"] is True


def test_hot_path_no_longer_calls_has_grant(db, two_users, server_manager, monkeypatch):
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Shared")
    wb_id = int(workbook["id"])
    manager.grant_workbook(owner.id, wb_id, viewer.id, ["view"])

    def boom(*args, **kwargs):
        raise AssertionError("hot path still calls _has_grant")

    monkeypatch.setattr(SumManager, "_has_grant", boom)

    result = manager.get_workbook(viewer.id, wb_id)
    assert result["my_permissions"]["reason"] == "view_only"


def test_view_grantee_cannot_mutate(db, two_users, server_manager, monkeypatch):
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Shared")
    wb_id = int(workbook["id"])
    manager.grant_workbook(owner.id, wb_id, viewer.id, ["view"])

    import pytest

    from src.core.suite.sum.manager import SumPermissionError

    with pytest.raises(SumPermissionError):
        manager.create_sheet(viewer.id, wb_id, "Nope")
