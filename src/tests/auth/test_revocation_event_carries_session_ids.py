"""Revocation alerts carry an authoritative subject and the revoked session ids.

The cross-worker socket close is only sound if the event it is driven by
carries (a) the user to revoke and (b) the exact auth sessions that were
revoked. These tests drive the real lifecycle helpers against a fake DB and
assert the payload that would be published to sibling workers.
"""

from types import SimpleNamespace

import pytest

import src.core.auth.managers.session.lifecycle as lifecycle
from src.core.auth.managers.session.lifecycle import LifecycleMixin

pytestmark = [pytest.mark.security, pytest.mark.unit]


def _stub(monkeypatch, rows, rowcount=1):
    """A minimal LifecycleMixin with a fake DB and captured emit call."""
    captured = {}
    monkeypatch.setattr(lifecycle, "invalidate_pattern", lambda *a, **k: None)
    monkeypatch.setattr(
        lifecycle, "close_session_gateway_connections", lambda *a, **k: None
    )
    monkeypatch.setattr(
        lifecycle, "close_user_gateway_connections", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "src.core.events.gateway_emit.emit_security_alert",
        lambda uid, action, detail=None: captured.update(
            user_id=uid, action=action, detail=detail
        ),
    )
    obj = object.__new__(LifecycleMixin)
    obj._db = SimpleNamespace(
        execute=lambda sql, params: SimpleNamespace(rowcount=rowcount),
        fetch_all=lambda sql, params: rows,
        fetch_one=lambda sql, params: None,
    )
    obj._log_audit = lambda *a, **k: None
    return obj, captured


def test_revoke_session_alert_carries_exact_session_id(monkeypatch):
    obj, captured = _stub(monkeypatch, rows=[])

    assert obj.revoke_session(42, 101) is True

    assert captured["user_id"] == 42
    assert captured["action"] == "session_revoked"
    assert captured["detail"]["revoke"] is True
    assert captured["detail"]["session_ids"] == [101]


def test_logout_all_alert_carries_every_revoked_session_id(monkeypatch):
    rows = [{"id": 101}, {"id": 202}]
    obj, captured = _stub(monkeypatch, rows=rows)

    assert obj.logout_all(42) == 2

    assert captured["detail"]["revoke"] is True
    assert captured["detail"]["session_ids"] == [101, 202]
