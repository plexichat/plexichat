"""PlexiSum permission-metadata tests.

The server never trusts client-supplied capability labels. These tests verify
that the effective access reported to a viewer (``can_view`` / ``can_edit`` /
``reason`` plus per-sheet ``read_only_ranges``) matches the server's actual
authorization decisions so the client can build permission-aware editing UI.
"""

import pytest

from src.core.suite.sum.manager import (
    SumManager,
    SumPermissionError,
)
from src.core.suite.sum.schema import create_tables as create_sum_tables


def _make_manager(db, server_manager):
    create_sum_tables(db)
    return SumManager(db, servers_module=server_manager)


def _write_cell(manager, user, workbook, sheet_id, value):
    manager.apply_operation(
        user.id,
        int(workbook["id"]),
        int(sheet_id),
        f"op-{value}-{workbook['id']}",
        "set_cell",
        0,
        [{"row": 0, "column": 0, "value_type": "text", "value": value}],
    )


def test_owner_sees_full_edit_permissions(db, two_users, server_manager):
    owner, _ = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Owner workbook")
    perms = manager._viewer_permissions(owner.id, int(workbook["id"]))
    assert perms == {
        "can_view": True,
        "can_edit": True,
        "reason": None,
        "read_only_ranges": [],
    }


def test_get_workbook_carries_permissions(db, two_users, server_manager):
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Shared view")
    manager.grant_workbook(owner.id, int(workbook["id"]), viewer.id, ["view"])

    response = manager.get_workbook(viewer.id, int(workbook["id"]))
    assert response["my_permissions"]["can_view"] is True
    assert response["my_permissions"]["can_edit"] is False
    assert response["my_permissions"]["reason"] == "view_only"


def test_view_grantee_reports_view_only(db, two_users, server_manager):
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Shared view")
    manager.grant_workbook(owner.id, int(workbook["id"]), viewer.id, ["view"])
    perms = manager._viewer_permissions(viewer.id, int(workbook["id"]))
    assert perms["can_view"] is True
    assert perms["can_edit"] is False
    assert perms["reason"] == "view_only"


def test_edit_grantee_can_edit(db, two_users, server_manager):
    owner, editor = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Shared edit")
    manager.grant_workbook(owner.id, int(workbook["id"]), editor.id, ["edit"])
    perms = manager._viewer_permissions(editor.id, int(workbook["id"]))
    assert perms["can_edit"] is True
    assert perms["reason"] is None
    # and the server actually permits the mutation
    sheet_id = workbook["sheets"][0]["id"]
    _write_cell(manager, editor, workbook, sheet_id, "edited")


def test_viewport_exposes_protected_ranges_to_non_owners(db, two_users, server_manager):
    owner, editor = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Protected")
    sheet_id = workbook["sheets"][0]["id"]
    manager.grant_workbook(owner.id, int(workbook["id"]), editor.id, ["edit"])
    manager.set_range_permission(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 5, 0, 3, "read_only"
    )

    viewport = manager.read_viewport(
        editor.id, int(workbook["id"]), int(sheet_id), 0, 1, 0, 1
    )
    # workbook-level access is edit, but the protected range is surfaced so
    # the client can block that specific area.
    assert viewport["can_edit"] is True
    assert len(viewport["read_only_ranges"]) == 1
    rng = viewport["read_only_ranges"][0]
    assert (
        rng["row_start"],
        rng["row_end"],
        rng["column_start"],
        rng["column_end"],
    ) == (0, 5, 0, 3)
    assert rng["permission"] == "read_only"

    # The owner is exempt from range protection and sees no protected ranges.
    owner_viewport = manager.read_viewport(
        owner.id, int(workbook["id"]), int(sheet_id), 0, 1, 0, 1
    )
    assert owner_viewport["read_only_ranges"] == []


def test_view_only_user_can_read_but_not_mutate(db, two_users, server_manager):
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Read only")
    sheet_id = workbook["sheets"][0]["id"]
    manager.grant_workbook(owner.id, int(workbook["id"]), viewer.id, ["view"])

    viewport = manager.read_viewport(
        viewer.id, int(workbook["id"]), int(sheet_id), 0, 1, 0, 1
    )
    assert viewport["can_edit"] is False
    assert viewport["read_only_ranges"] == []

    with pytest.raises(SumPermissionError):
        manager.apply_operation(
            viewer.id,
            int(workbook["id"]),
            int(sheet_id),
            "forbidden-op",
            "set_cell",
            0,
            [{"row": 0, "column": 0, "value_type": "text", "value": "nope"}],
        )


def test_list_workbooks_reports_real_permissions(db, two_users, server_manager):
    """The workbook listing must not fabricate a full-edit grant.

    Regression guard: ``list_workbooks`` omitted ``my_permissions``, so the
    API response schema's default (can_view/can_edit = true) told a view-only
    grantee they could edit every listed workbook.
    """
    owner, viewer = two_users
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(owner.id, "Listed")
    manager.grant_workbook(owner.id, int(workbook["id"]), viewer.id, ["view"])

    owner_items, _ = manager.list_workbooks(owner.id)
    viewer_items, _ = manager.list_workbooks(viewer.id)

    assert owner_items[0]["my_permissions"]["can_edit"] is True
    assert viewer_items[0]["my_permissions"]["can_view"] is True
    assert viewer_items[0]["my_permissions"]["can_edit"] is False
    assert viewer_items[0]["my_permissions"]["reason"] == "view_only"


def test_workbook_response_schema_fails_closed(db):
    """An unpopulated permissions projection must deny, not grant."""
    from src.api.schemas.sum import SumViewerPermissionsResponse, SumViewportResponse

    defaults = SumViewerPermissionsResponse()
    assert defaults.can_view is False
    assert defaults.can_edit is False

    # A minimal viewport payload (production always populates can_edit).
    viewport = SumViewportResponse.model_validate(
        {
            "workbook_id": "1",
            "sheet_id": "2",
            "revision": 0,
            "row_start": 0,
            "row_end": 1,
            "column_start": 0,
            "column_end": 1,
        }
    )
    assert viewport.can_edit is False


def test_server_member_without_manage_is_view_only(db, two_users, server_manager):
    owner, member = two_users
    server = server_manager.create_server(owner.id, "Suite server")
    server_manager.add_member(server.id, member.id)
    manager = _make_manager(db, server_manager)
    workbook = manager.create_workbook(
        owner.id, "Server workbook", scope="server", server_id=server.id
    )
    perms = manager._viewer_permissions(member.id, int(workbook["id"]))
    assert perms["can_view"] is True
    assert perms["can_edit"] is False
    assert perms["reason"] == "view_only"
