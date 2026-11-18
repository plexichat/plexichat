"""Regression tests for telemetry/audit hardening boundaries.

Kept next to the modules under test so the fixes stay covered without depending
on the wider test-suite fixtures.
"""

import itertools
import json

import pytest

import src.core.telemetry as telemetry
from src.core.telemetry.audit import AuditChain
from src.core.telemetry.scrubbing import REDACTED


class _DB:
    def __init__(self):
        self.rows = []

    def execute(self, sql, params=()):
        pass

    def execute_many(self, sql, rows):
        self.rows.extend(rows)

    def fetch_one(self, sql, params=()):
        return {"c": 0}

    def fetch_all(self, sql, params=()):
        return []


@pytest.fixture
def telemetry_db(monkeypatch):
    db = _DB()
    monkeypatch.setattr(telemetry, "_db", db)
    monkeypatch.setattr(telemetry, "_setup_complete", True)
    monkeypatch.setattr(telemetry, "_seen_endpoints", set())
    monkeypatch.setattr(telemetry, "_cardinality_rejections", 0)
    monkeypatch.setattr(telemetry, "_route_templates", [])
    monkeypatch.setattr(telemetry, "_templates_registered", False)
    # generate_snowflake_id needs a configured logger; use deterministic IDs.
    import src.utils.encryption as enc

    ids = itertools.count(1)
    monkeypatch.setattr(enc, "generate_snowflake_id", lambda: next(ids))
    return db


class TestMethodCardinality:
    def test_unknown_methods_collapse_into_one_bucket(self, telemetry_db):
        telemetry.register_route_templates(["/api/v1/x"])
        entries = [
            {
                "endpoint": "/api/v1/x",
                "method": f"M{i:05d}",
                "response_time_ms": 1.0,
                "status_code": 200,
            }
            for i in range(100)
        ]

        assert telemetry.submit_response_times(entries) == 100
        assert {row[2] for row in telemetry_db.rows} == {telemetry.OVERFLOW_METHOD}
        stats = telemetry.get_cardinality_stats()
        assert stats["cardinality_rejections"] == 100
        assert stats["distinct_endpoints"] == 1

    def test_known_methods_are_preserved(self, telemetry_db):
        entries = [
            {
                "endpoint": "/api/v1/x",
                "method": method,
                "response_time_ms": 1.0,
                "status_code": 200,
            }
            for method in ("GET", "POST", "DELETE")
        ]

        assert telemetry.submit_response_times(entries) == 3
        assert {row[2] for row in telemetry_db.rows} == {"GET", "POST", "DELETE"}
        assert telemetry.get_cardinality_stats()["cardinality_rejections"] == 0


class TestAuditMetadataRedaction:
    def test_secret_in_metadata_never_reaches_the_file(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        chain = AuditChain(
            str(path), head_path=str(path) + ".head", durable=False
        )
        secret = "s3cr3t-do-not-leak"

        chain.append(
            "user.ban",
            f"Bearer {secret}",
            status=f"ok token={secret}",
            target_id="42",
            details="clean",
        )

        raw = path.read_text(encoding="utf-8")
        assert secret not in raw
        assert REDACTED in raw
        assert secret not in json.dumps(chain.read_all()[0])
        assert chain.verify_chain() == (True, 1, None)

    def test_plain_metadata_is_unchanged(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        chain = AuditChain(
            str(path), head_path=str(path) + ".head", durable=False
        )

        chain.append("login", 42, details="ok")

        entry = chain.read_all()[0]["entry"]
        assert entry["action"] == "login"
        assert entry["actor"] == "42"
        assert entry["status"] == "success"
