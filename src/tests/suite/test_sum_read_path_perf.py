"""Regression tests for the PlexiSum hot read paths.

Sheet metadata, range-permission sheet checks and CSV export used to serialize
the whole workbook into a container and reopen it (replaying additive schema
DDL) on every request. These tests pin both the observable behaviour and the
fact that the read paths stay on the already-materialized live connection.
"""

import pytest

from src.core.suite.sum.manager import SumManager
from src.core.suite.sum.schema import create_tables


def _make_manager(db, server_manager):
    create_tables(db)
    return SumManager(db, servers_module=server_manager)


def _boom(*args, **kwargs):
    raise AssertionError("hot read path reserialized or reopened the workbook")


def test_get_workbook_sheets_match_file_projection(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Sheets")
    wb_id = int(workbook["id"])

    response = manager.get_workbook(owner.id, wb_id)
    expected = manager._read_sheets_from_file(manager._file_bytes(wb_id))
    assert response["sheets"] == expected
    assert [sheet["name"] for sheet in response["sheets"]] == ["Sheet 1"]

    manager.create_sheet(owner.id, wb_id, "Second")
    response = manager.get_workbook(owner.id, wb_id)
    expected = manager._read_sheets_from_file(manager._file_bytes(wb_id))
    assert response["sheets"] == expected
    assert [sheet["name"] for sheet in response["sheets"]] == ["Sheet 1", "Second"]


def test_get_workbook_stays_on_live_connection(
    db, two_users, server_manager, monkeypatch
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Live")
    wb_id = int(workbook["id"])

    monkeypatch.setattr(SumManager, "_serialize_connection", _boom)
    monkeypatch.setattr(SumManager, "_open_sqlite_memory", _boom)

    response = manager.get_workbook(owner.id, wb_id)
    assert [sheet["name"] for sheet in response["sheets"]] == ["Sheet 1"]
    assert response["my_permissions"]["can_edit"] is True


def test_range_permission_sheet_check_stays_on_live_connection(
    db, two_users, server_manager, monkeypatch
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Ranges")
    wb_id = int(workbook["id"])
    sheet_id = int(workbook["sheets"][0]["id"])

    monkeypatch.setattr(SumManager, "_serialize_connection", _boom)
    monkeypatch.setattr(SumManager, "_open_sqlite", _boom)

    listed = manager.list_range_permissions(owner.id, wb_id, sheet_id)
    assert listed == []

    created = manager.set_range_permission(
        owner.id, wb_id, sheet_id, 0, 5, 0, 3, "read_only"
    )
    assert created["permission"] == "read_only"
    listed = manager.list_range_permissions(owner.id, wb_id, sheet_id)
    assert len(listed) == 1
    assert listed[0]["id"] == created["id"]


def test_range_permission_still_rejects_unknown_sheet(
    db, two_users, server_manager, monkeypatch
):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Ranges")
    wb_id = int(workbook["id"])

    monkeypatch.setattr(SumManager, "_serialize_connection", _boom)
    monkeypatch.setattr(SumManager, "_open_sqlite", _boom)

    from src.core.suite.sum.manager import SumNotFoundError

    with pytest.raises(SumNotFoundError):
        manager.list_range_permissions(owner.id, wb_id, 999999)
    with pytest.raises(SumNotFoundError):
        manager.set_range_permission(owner.id, wb_id, 999999, 0, 5, 0, 3, "read_only")
