"""Regression tests for trimmed PlexiSum snapshot/file read paths.

Snapshot creation used to reopen the workbook in a fresh temp SQLite database
just to count cells; snapshot restore and file upload re-read the workbook
metadata row twice after already fetching it for authorization. These tests
pin the behaviour and the absence of those redundant reads.
"""

from src.core.suite.sum.manager import SumManager, SumStorageError
from src.core.suite.sum.schema import create_tables


class _WorkbookRefetchSpy:
    """Count duplicate workbook-metadata reads while delegating to the DB."""

    def __init__(self, db):
        self._db = db
        self.refetches = 0

    def __getattr__(self, name):
        return getattr(self._db, name)

    def fetch_one(self, query, params=None):
        if (
            "FROM sum_workbooks" in query
            and "deleted = 0" not in query
            and "COUNT(" not in query
        ):
            self.refetches += 1
        return self._db.fetch_one(query, params)


def _make_manager(db, server_manager):
    create_tables(db)
    return SumManager(db, servers_module=server_manager)


def _boom(*args, **kwargs):
    raise AssertionError("read path reopened the workbook in a fresh database")


def test_create_snapshot_counts_cells_on_live_connection(
    db, two_users, server_manager, monkeypatch
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Snapshot")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])
    manager.apply_operation(
        owner.id,
        wb_id,
        sheet_id,
        "snap-1",
        "set_cell",
        0,
        [
            {"row": 0, "column": 0, "value_type": "number", "value": 1},
            {"row": 1, "column": 0, "value_type": "number", "value": 2},
        ],
    )

    monkeypatch.setattr(SumManager, "_open_sqlite", _boom)
    snapshot = manager.create_snapshot(owner.id, wb_id)
    assert snapshot["cell_count"] == 2
    assert snapshot["workbook_id"] == str(wb_id)


def test_create_snapshot_still_rejects_corrupt_source(
    db, two_users, server_manager, monkeypatch
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Snapshot")
    wb_id = int(workbook["id"])

    def broken(*args, **kwargs):
        raise __import__("sqlite3").OperationalError("corrupt")

    import pytest

    monkeypatch.setattr(SumManager, "_live_connection", broken)
    with pytest.raises(SumStorageError):
        manager.create_snapshot(owner.id, wb_id)


def test_restore_snapshot_does_not_refetch_workbook(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Restore")
    wb_id = int(workbook["id"])
    snapshot = manager.create_snapshot(owner.id, wb_id)

    spy = _WorkbookRefetchSpy(db)
    manager._db = spy
    restored = manager.restore_snapshot(owner.id, wb_id, int(snapshot["id"]))

    assert spy.refetches == 0
    assert restored["id"] == str(wb_id)
    assert restored["current_revision"] >= 1


def test_upload_file_does_not_refetch_workbook(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Upload")
    wb_id = int(workbook["id"])
    payload = manager._file_bytes(wb_id)

    spy = _WorkbookRefetchSpy(db)
    manager._db = spy
    result = manager.upload_file(owner.id, wb_id, payload)

    assert spy.refetches == 0
    assert result["revision"] >= 1
    assert result["workbook_id"] == str(wb_id)
