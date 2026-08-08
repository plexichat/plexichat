"""Targeted tests for ``src.utils.system_alerts.record_system_alert``."""

import pytest

from src.utils.system_alerts import record_system_alert


class FakeDb:
    def __init__(self):
        self.calls = []

    def execute(self, sql, params):
        self.calls.append((sql, params))


def test_no_db_is_noop(monkeypatch):
    monkeypatch.setattr("src.api.get_db", lambda: None)
    # Must not raise, and returns None.
    assert record_system_alert("connected", {"k": 1}) is None


def test_inserts_alert_with_defaults(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("src.api.get_db", lambda: db)
    record_system_alert("connected", {"k": "v"}, source="valkey", severity="error")
    assert len(db.calls) == 1
    sql, params = db.calls[0]
    assert "system_alerts" in sql
    source, event_type, severity, details, target_path, created_at = params
    assert source == "valkey"
    assert event_type == "connected"
    assert severity == "error"
    assert details == '{"k": "v"}'
    assert target_path is None
    assert isinstance(created_at, int)


def test_inserts_with_target_path(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("src.api.get_db", lambda: db)
    record_system_alert("dead", target_path="/admin/alerts")
    _, params = db.calls[0]
    assert params[4] == "/admin/alerts"


def test_none_details_stored_as_null(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("src.api.get_db", lambda: db)
    record_system_alert("connected")
    _, params = db.calls[0]
    assert params[3] is None


@pytest.mark.parametrize(
    "details",
    [
        {"a": 1},
        [],
        "plain",
        42,
        None,
        {"nested": {"deep": [1, 2]}},
    ],
)
def test_details_serialized(monkeypatch, details):
    db = FakeDb()
    monkeypatch.setattr("src.api.get_db", lambda: db)
    record_system_alert("x", details)
    _, params = db.calls[0]
    if details is None:
        assert params[3] is None
    else:
        import json

        assert json.loads(params[3]) == details


def test_db_execute_error_swallowed(monkeypatch):
    class ExplodingDb:
        def execute(self, sql, params):
            raise RuntimeError("db down")

    monkeypatch.setattr("src.api.get_db", lambda: ExplodingDb())
    assert record_system_alert("x") is None  # must not raise


def test_non_serializable_details_swallowed(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("src.api.get_db", lambda: db)

    class NotSerializable:
        pass

    record_system_alert(
        "x", {"bad": NotSerializable()}
    )  # json.dumps fails -> swallowed
    assert db.calls == []


def test_default_source_and_severity(monkeypatch):
    db = FakeDb()
    monkeypatch.setattr("src.api.get_db", lambda: db)
    record_system_alert("boot")
    _, params = db.calls[0]
    assert params[0] == "cross_worker"
    assert params[2] == "info"
