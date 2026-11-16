"""Regression: secrets never survive into an audit/log payload.

The recursive scrubber must cover nested structures, free text, JSON embedded
in a string, and every surface that serves logged data (the audit store, the
admin audit routes, and the admin log viewer).
"""

import json
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.core.telemetry.audit import AuditChain
from src.core.telemetry.scrubbing import REDACTED, redact_secrets

_SECRET = "s3cr3t-do-not-leak"


def test_redact_secrets_recurses_through_nested_containers():
    payload = {
        "reason": "spam",
        "password": _SECRET,
        "nested": {
            "api_key": _SECRET,
            "items": [
                {"Authorization": f"Bearer {_SECRET}"},
                {"client_secret": _SECRET},
                "plain value",
            ],
        },
        "author": "someone",  # must NOT be redacted (tokenized key match)
    }

    clean = redact_secrets(payload)

    assert clean["reason"] == "spam"
    assert clean["author"] == "someone"
    assert clean["password"] == REDACTED
    assert clean["nested"]["api_key"] == REDACTED
    assert clean["nested"]["items"][0]["Authorization"] == REDACTED
    assert clean["nested"]["items"][1]["client_secret"] == REDACTED
    assert clean["nested"]["items"][2] == "plain value"


def test_redact_secrets_strips_secret_patterns_from_text():
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456ghi789"
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA\n"
        "-----END RSA PRIVATE KEY-----"
    )
    text = (
        f"Authorization: Bearer {_SECRET} token={_SECRET} "
        f"jwt={jwt} key=sk_live_abcdefghijklmnop {pem}"
    )

    clean = redact_secrets(text)

    assert _SECRET not in clean
    assert jwt not in clean
    assert "sk_live_abcdefghijklmnop" not in clean
    assert "MIIEowIBAAKCAQEA" not in clean
    assert REDACTED in clean


def test_redact_secrets_handles_json_embedded_in_details():
    details = json.dumps(
        {"user_password": _SECRET, "note": "ban", "inner": {"token": _SECRET}}
    )
    clean = redact_secrets(details)
    parsed = json.loads(clean)
    assert parsed["user_password"] == REDACTED
    assert parsed["note"] == "ban"
    assert parsed["inner"]["token"] == REDACTED


def test_redact_secrets_is_bounded():
    deep: dict = {"value": "leaf"}
    for _ in range(30):
        deep = {"level": deep}
    assert redact_secrets(deep)["level"] is not None  # no recursion error

    long_text = "a" * 20_000
    assert len(redact_secrets(long_text)) <= 4_200


def test_chain_scrubs_before_persisting_and_on_read(tmp_path):
    chain = AuditChain(
        str(tmp_path / "audit.jsonl"),
        head_path=str(tmp_path / "audit.jsonl.head"),
        durable=False,
    )
    chain.append(
        "user.ban",
        42,
        details=f"reason=spam password={_SECRET}",
        access_token=_SECRET,
    )

    raw = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert _SECRET not in raw
    clean = chain.read_all()[0]
    assert _SECRET not in json.dumps(clean)
    assert clean["entry"]["payload"]["details"].endswith(REDACTED)


def test_admin_log_viewer_scrubs_served_lines(tmp_path, monkeypatch):
    from src.core.admin import logs as admin_logs

    monkeypatch.setattr(admin_logs, "get_log_dir", lambda: tmp_path)
    (tmp_path / "server.log").write_text(
        f"2026-11-08 12:00:00,000 - INFO - token={_SECRET}\n", encoding="utf-8"
    )

    result = admin_logs.read_log_lines("server.log", limit=10)

    assert _SECRET not in result["lines"][0]["message"]
    assert _SECRET not in result["lines"][0]["raw"]


def test_admin_audit_routes_scrub_details_and_export():
    from src.api.routes.admin import audit as audit_routes

    row = {
        "id": 1,
        "admin_id": 2,
        "action": "user.ban",
        "target_type": "user",
        "target_id": 7,
        "details": f"reason=spam password={_SECRET}",
        "ip_address": "1.2.3.4",
        "user_agent": f"curl token={_SECRET}",
        "status": "success",
        "created_at": 1700000000000,
    }

    class _DB:
        def fetch_one(self, query, params=None):
            return {"total": 1}

        def fetch_all(self, query, params=None):
            return [dict(row)]

    app = FastAPI()
    app.include_router(audit_routes.router, prefix="/api/v1/admin")
    client = TestClient(app)
    ctx = (
        patch.object(audit_routes, "check_host_restriction"),
        patch.object(audit_routes, "require_admin_permission"),
        patch("src.api.get_db", return_value=_DB()),
    )
    for c in ctx:
        c.start()
    try:
        listed = client.get("/api/v1/admin/audit/logs?action_family=user")
        assert listed.status_code == 200
        entry = listed.json()["logs"][0]
        assert _SECRET not in entry["details"]
        assert _SECRET not in entry["user_agent"]

        exported = client.get(
            "/api/v1/admin/audit/logs/export?format=csv&action_family=user"
        )
        assert exported.status_code == 200
        assert _SECRET not in exported.text
    finally:
        for c in reversed(ctx):
            c.stop()
