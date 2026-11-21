"""Workbook lifecycle (delete/restore) must require owner-level authority.

A workbook ``edit`` grant lets a collaborator change cells, but it must not
let them destroy or resurrect the workbook itself.  ``delete_workbook`` and
``restore_workbook`` previously relied on ``_workbook(..., mutation=True)`` /
``_authorize(..., mutation=True)``, which treat an ``edit`` grant as sufficient
for *any* mutation.  That let an editor soft-delete a workbook they do not own,
archiving its platform artifact and purging materialized operations, or restore
a workbook the owner deliberately deleted.

Sharing is the other owner-only act on a workbook (see ``grant_workbook``), and
these lifecycle transitions are at least as destructive.  This test pins the
same owner/server-manager authority gate for them.
"""

import pytest

from src.core.suite.sum.manager import (
    SumManager,
    SumNotFoundError,
    SumPermissionError,
)
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _make_manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def test_edit_grantee_cannot_delete_workbook(db, two_users, server_manager):
    owner, editor = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Owned workbook")
    wb_id = int(workbook["id"])
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])

    with pytest.raises(SumPermissionError):
        manager.delete_workbook(editor.id, wb_id)

    # The owner can still delete, and the workbook really was left live.
    assert manager.get_workbook(owner.id, wb_id)["id"] == str(wb_id)
    manager.delete_workbook(owner.id, wb_id)
    with pytest.raises(SumNotFoundError):
        manager.get_workbook(owner.id, wb_id)


def test_edit_grantee_cannot_restore_deleted_workbook(db, two_users, server_manager):
    owner, editor = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Recoverable workbook")
    wb_id = int(workbook["id"])
    manager.grant_workbook(owner.id, wb_id, editor.id, ["edit"])
    manager.delete_workbook(owner.id, wb_id)

    with pytest.raises(SumPermissionError):
        manager.restore_workbook(editor.id, wb_id)

    # The owner retains restore authority.
    restored = manager.restore_workbook(owner.id, wb_id)
    assert restored["id"] == str(wb_id)
