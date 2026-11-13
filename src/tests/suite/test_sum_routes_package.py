"""Package-boundary tests for the PlexiSum route split."""

from __future__ import annotations

import asyncio
import types

from fastapi.routing import APIRoute

from src.api.routes.suite import sum as pkg

HANDLERS = (
    "list_workbooks",
    "create_workbook",
    "create_from_template",
    "set_pinned",
    "set_template",
    "get_workbook",
    "delete_workbook",
    "restore_workbook",
    "read_viewport",
    "apply_operation",
    "sort_range",
    "replay_operations",
    "create_snapshot",
    "list_snapshots",
    "restore_snapshot",
    "create_sheet",
    "rename_sheet",
    "delete_sheet",
    "download_file",
    "upload_file",
    "export_csv",
    "import_csv",
    "list_grants",
    "grant_workbook",
    "revoke_grant",
    "list_range_permissions",
    "set_range_permission",
    "remove_range_permission",
    "list_presence",
    "set_presence",
    "get_freeze",
    "set_freeze",
    "list_validations",
    "set_validation",
    "delete_validation",
    "list_conditional_formats",
    "set_conditional_format",
    "delete_conditional_format",
    "list_comments",
    "add_comment",
    "resolve_comment",
    "delete_comment",
    "export_xlsx",
    "import_xlsx",
    "list_features",
    "create_feature",
    "update_feature",
    "delete_feature",
)


class TestCompatSurface:
    def test_router_is_the_registered_one(self) -> None:
        from src.api.routes import sum_router

        assert pkg.router is sum_router

    def test_every_handler_is_reexported(self) -> None:
        for name in HANDLERS:
            assert callable(getattr(pkg, name)), name

    def test_router_shape_is_unchanged(self) -> None:
        assert pkg.router.prefix == "/suite/sum"
        assert pkg.router.tags == ["PlexiSum"]
        routes = [route for route in pkg.router.routes if isinstance(route, APIRoute)]
        assert len(routes) == len(HANDLERS)


class TestPatchPoints:
    def test_manager_patch_reaches_the_workbook_handler(self, monkeypatch) -> None:
        """``sum_routes._manager`` stays the monkeypatch point for handlers."""
        captured: dict[str, object] = {}

        class FakeManager:
            def list_workbooks(
                self, user_id, limit, offset, template=False, search=None
            ):
                captured["user_id"] = user_id
                captured["search"] = search
                return [], 0

        monkeypatch.setattr(pkg, "_manager", lambda: FakeManager())
        asyncio.run(
            pkg.list_workbooks(
                limit=10,
                offset=0,
                template=False,
                search="budget",
                current_user=types.SimpleNamespace(user_id=42),
            )
        )

        assert captured == {"user_id": 42, "search": "budget"}

    def test_capability_patch_point_is_exported(self) -> None:
        assert callable(pkg.get_capability)
        assert callable(pkg.is_usable)
