"""Export generation quotas and fail-closed partial-failure handling."""

from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

QUOTAS = {
    "max_records_per_category": 100,
    "max_total_records": 200,
    "max_export_bytes": 100_000,
}


class TestQuotaHelpers:
    def test_record_counts_per_category(self):
        from src.core.dsar.privacy import export_record_counts

        data = {
            "user_id": 1,
            "messages": {"messages": [{"id": 1}, {"id": 2}]},
            "identity": {"id": 1, "username": "a"},
        }
        counts = export_record_counts(data)
        assert counts == {"messages": 2, "identity": 1}

    @pytest.mark.parametrize(
        "overrides",
        [
            {"max_records_per_category": 1},
            {"max_total_records": 1},
            {"max_export_bytes": 10},
        ],
    )
    def test_each_bound_is_enforced(self, overrides):
        from src.core.dsar.privacy import (
            ExportQuotaExceeded,
            enforce_export_quotas,
        )

        data = {"messages": {"messages": [{"id": 1}, {"id": 2}]}}
        quotas = dict(QUOTAS, **overrides)
        with pytest.raises(ExportQuotaExceeded):
            enforce_export_quotas(data, **quotas)

    def test_within_quotas_is_allowed(self):
        from src.core.dsar.privacy import enforce_export_quotas

        enforce_export_quotas({"identity": {"id": 1}}, **QUOTAS)

    def test_incomplete_collection_fails_closed(self):
        from src.core.dsar.privacy import (
            ExportIncompleteError,
            assert_export_complete,
        )

        with pytest.raises(ExportIncompleteError):
            assert_export_complete(
                {"export_metadata": {"complete": False, "warnings": ["boom"]}}
            )
        assert_export_complete({"identity": {"id": 1}})

    def test_config_quotas_clamp_non_positive_values(self):
        from src.core.dsar.config import export_quotas

        resolved = export_quotas(
            {"export_quotas": {"max_export_bytes": 0, "max_total_records": -5}}
        )
        assert resolved["max_export_bytes"] >= 1
        assert resolved["max_total_records"] >= 1


class TestGenerationFailClosed:
    @pytest.fixture
    def approved_request(self, db, auth_manager, pii_gen):
        from unittest.mock import patch

        from src.core.dsar import setup as dsar_setup
        from src.core.dsar import request_data_export
        from src.utils import encryption

        dsar_setup(db)
        with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
            user = auth_manager.register(
                username=f"quota_{uuid.uuid4().hex[:10]}",
                email=pii_gen.email(),
                password="TestPass123!",
            )
        req = request_data_export(user.id, format="json")
        db.execute(
            "UPDATE dsar_requests SET status = 'approved' WHERE id = ?", (req["id"],)
        )
        return db, req["id"]

    def test_oversized_export_fails_generation(self, approved_request, monkeypatch):
        import src.core.dsar.manager as manager_module
        from src.core.dsar.privacy import ExportQuotaExceeded

        db, req_id = approved_request
        monkeypatch.setattr(
            manager_module,
            "export_quotas",
            lambda *a, **k: dict(QUOTAS, max_export_bytes=1),
        )
        with pytest.raises(ExportQuotaExceeded):
            manager_module.DSARManager(db).generate_manual(req_id, admin_id=1)

        row = db.fetch_one("SELECT status FROM dsar_requests WHERE id = ?", (req_id,))
        assert row["status"] == "failed"

    def test_partial_collection_fails_generation(self, approved_request, monkeypatch):
        import src.core.dsar.manager as manager_module
        from src.core.dsar.collector import DataCollector
        from src.core.dsar.privacy import ExportIncompleteError

        db, req_id = approved_request

        def broken_collect(self, user_id):
            return {
                "user_id": user_id,
                "export_metadata": {
                    "complete": False,
                    "warnings": ["messages: query failed"],
                },
            }

        monkeypatch.setattr(DataCollector, "collect_all", broken_collect)
        with pytest.raises(ExportIncompleteError):
            manager_module.DSARManager(db).generate_manual(req_id, admin_id=1)

        row = db.fetch_one("SELECT status FROM dsar_requests WHERE id = ?", (req_id,))
        assert row["status"] == "failed"
