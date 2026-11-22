"""Regression tests for the admin oversight route performance work.

The admin artifacts / data-export / plexijoin endpoints were doing redundant
work on their hot paths. These tests pin the cheaper behaviour while making
sure the permission/existence semantics are unchanged.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.schemas.common import SnowflakeIDParam
from src.api.routes.admin import artifacts as artifacts_routes
from src.api.routes.admin import data_export as data_export_routes
from src.api.routes.admin import plexijoin as plexijoin_routes


def _request(query: bytes = b"") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/v1/admin/artifacts",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "scheme": "http",
            "server": ("testserver", 80),
            "query_string": query,
        }
    )


def _artifact_row(row_id: int, total: int) -> dict:
    return {
        "id": row_id,
        "author_id": 7,
        "artifact_type": "upload",
        "title": "title",
        "status": "completed",
        "payload": "{}",
        "created_at": 1,
        "updated_at": 1,
        "rev": 0,
        "_total": total,
    }


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_all(self, query, params=None):
        self.calls.append((query, params))
        return self.rows


class TestArtifactListBatching:
    def test_page_and_total_come_from_one_query(self):
        db = _FakeDB([_artifact_row(1, 3), _artifact_row(2, 3)])
        items, total = artifacts_routes._list_with_total(db, 100, 0)

        assert len(db.calls) == 1
        assert "(SELECT COUNT(*) FROM artifacts) AS _total" in db.calls[0][0]
        assert "COUNT(*) OVER()" not in db.calls[0][0]
        assert db.calls[0][1] == (100, 0)
        assert total == 3
        assert [int(a.id) for a in items] == [1, 2]

    def test_empty_page_has_no_total(self):
        db = _FakeDB([])
        items, total = artifacts_routes._list_with_total(db, 100, 500)
        assert items == []
        assert total is None

    def test_endpoint_falls_back_to_count_on_empty_page(self, monkeypatch):
        monkeypatch.setattr(artifacts_routes, "check_host_restriction", lambda r: None)
        monkeypatch.setattr(artifacts_routes, "get_admin_from_token", lambda r: 1)
        monkeypatch.setattr(
            artifacts_routes, "require_admin_permission", lambda r, p: 1
        )
        monkeypatch.setattr(
            artifacts_routes, "_list_with_total", lambda db, limit, offset: ([], None)
        )
        manager = MagicMock()
        manager.list_with_filters.return_value = []
        manager.count.return_value = 0
        monkeypatch.setattr(artifacts_routes, "_get_manager", lambda: manager)

        result = asyncio.run(
            artifacts_routes.admin_list_artifacts(_request(b"limit=10&offset=999"))
        )
        assert result == {"total": 0, "items": []}
        manager.count.assert_called_once()


class TestArtifactDeleteGate:
    def _patch(self, monkeypatch, manager):
        monkeypatch.setattr(artifacts_routes, "check_host_restriction", lambda r: None)
        monkeypatch.setattr(
            artifacts_routes, "require_admin_permission", lambda r, p: 1
        )

        approval_calls = []

        def _require_approved(action, **kwargs):
            # Mirror the production call:
            # require_approved_request("artifacts.force_delete",
            #                          target_context=(artifact_id, admin_id)).
            # Record the binding so the approval requirement and its exact
            # target stay asserted instead of being mocked into vacuity.
            approval_calls.append((action, kwargs))
            return 9001

        monkeypatch.setattr(
            artifacts_routes, "require_approved_request", _require_approved
        )
        monkeypatch.setattr(artifacts_routes, "_get_manager", lambda: manager)
        return approval_calls

    def test_success_path_skips_preflight_get(self, monkeypatch):
        manager = MagicMock()
        manager.delete.return_value = True
        approval_calls = self._patch(monkeypatch, manager)

        result = asyncio.run(artifacts_routes.admin_delete_artifact("123", _request()))

        assert result == {"success": True, "id": "123"}
        assert approval_calls == [
            ("artifacts.force_delete", {"target_context": (123, 1)})
        ]
        manager.get.assert_not_called()
        manager.delete.assert_called_once_with(123, purging_user_id=1)

    def test_missing_artifact_still_returns_404(self, monkeypatch):
        manager = MagicMock()
        manager.delete.return_value = False
        manager.get.return_value = None
        approval_calls = self._patch(monkeypatch, manager)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(artifacts_routes.admin_delete_artifact("123", _request()))
        assert exc.value.status_code == 404
        assert approval_calls == [
            ("artifacts.force_delete", {"target_context": (123, 1)})
        ]
        manager.get.assert_called_once_with(123)

    def test_failed_delete_of_existing_artifact_returns_500(self, monkeypatch):
        manager = MagicMock()
        manager.delete.return_value = False
        manager.get.return_value = object()
        approval_calls = self._patch(monkeypatch, manager)

        with pytest.raises(HTTPException) as exc:
            asyncio.run(artifacts_routes.admin_delete_artifact("123", _request()))
        assert exc.value.status_code == 500
        assert approval_calls == [
            ("artifacts.force_delete", {"target_context": (123, 1)})
        ]


class TestDataExportMutationErrors:
    def test_not_found_maps_to_404(self):
        exc = data_export_routes._handle_mutation_error(
            42, ValueError("DSAR request 42 not found")
        )
        assert exc.status_code == 404

    def test_status_error_still_maps_to_400(self):
        exc = data_export_routes._handle_mutation_error(
            42, ValueError("Cannot approve request in status: pending")
        )
        assert exc.status_code == 400

    def test_approve_skips_redundant_existence_query(self, monkeypatch):
        monkeypatch.setattr(
            data_export_routes, "check_host_restriction", lambda r: None
        )
        monkeypatch.setattr(
            data_export_routes, "require_admin_permission", lambda r, p: 1
        )

        def _no_preflight(_request_id):
            raise AssertionError("redundant existence query must not run")

        monkeypatch.setattr(data_export_routes, "_get_request_or_404", _no_preflight)
        monkeypatch.setattr(data_export_routes, "approve_request", lambda *a: {})

        result = asyncio.run(
            data_export_routes.approve_data_export(_request(), SnowflakeIDParam(42))
        )
        assert result.success is True

    def test_not_found_from_core_still_returns_404(self, monkeypatch):
        monkeypatch.setattr(
            data_export_routes, "check_host_restriction", lambda r: None
        )
        monkeypatch.setattr(
            data_export_routes, "require_admin_permission", lambda r, p: 1
        )

        def _missing(*_args):
            raise ValueError("DSAR request 42 not found")

        monkeypatch.setattr(data_export_routes, "approve_request", _missing)
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                data_export_routes.approve_data_export(_request(), SnowflakeIDParam(42))
            )
        assert exc.value.status_code == 404


class TestPlexiJoinEncryptionAdapter:
    def test_adapter_is_reused(self, monkeypatch):
        sentinel = object()
        monkeypatch.setattr(plexijoin_routes, "_ENCRYPTION_SERVICE", sentinel)
        assert plexijoin_routes._encryption_service() is sentinel
