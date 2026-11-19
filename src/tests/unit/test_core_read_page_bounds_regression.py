"""Regression: core read APIs bound their pagination limit at the manager edge.

Two direct-call read paths bound ``limit`` straight into SQLite ``LIMIT ?``
with no validation of their own:

* ``ThreadManager.get_archived_threads`` (also exposed as the public
  ``src.core.threads.get_archived_threads``) — internal callers/tests bypass
  the HTTP route's ``ge=1`` cap.
* ``AuditMixin.get_login_history`` / ``get_security_events`` (exposed as the
  module-level ``src.core.auth`` APIs, with no route in front of them).

SQLite reads ``LIMIT -1`` as *unlimited*, so a negative limit from any of these
callers turned one bounded page into a full-table read (the audit case dumping
decrypted IP/device PII). The manager boundary must reject/ bound the value so
the page contract holds regardless of whether a route validated it.
"""

from unittest.mock import Mock

import pytest

from src.core.security.hardening import HardeningViolation
from src.core.threads.manager import ThreadManager
from src.core.auth.managers.audit import AuditMixin, _MAX_AUDIT_PAGE

pytestmark = pytest.mark.unit


# --- Thread archived listing ------------------------------------------------


class _ThreadDB:
    def __init__(self):
        self.calls = []

    def fetch_one(self, sql, params=()):
        if "srv_channels" in sql:
            return {"id": 1, "server_id": 5, "deleted": 0}
        return None

    def fetch_all(self, sql, params=()):
        self.calls.append((sql, params))
        return []

    def execute(self, sql, params=()):
        pass


def _thread_manager(db, grant=True):
    mgr = ThreadManager(db)
    servers = Mock()
    servers.has_permission = Mock(return_value=grant)
    mgr._servers = servers
    return mgr


@pytest.mark.parametrize("bad_limit", [-1, -100, 10**9, "abc", float("nan"), True])
def test_archived_threads_rejects_unbounded_or_malformed_limit(bad_limit):
    db = _ThreadDB()
    mgr = _thread_manager(db)

    with pytest.raises(HardeningViolation):
        mgr.get_archived_threads(42, 1, limit=bad_limit)

    # The bad value must never reach SQL: no channel lookup / page query ran.
    assert db.calls == []


def test_archived_threads_valid_limit_still_forwarded():
    db = _ThreadDB()
    mgr = _thread_manager(db)

    assert mgr.get_archived_threads(42, 1, limit=25) == []

    assert len(db.calls) == 1
    assert db.calls[0][1][-1] == 25


# --- Auth audit history -----------------------------------------------------


class _AuditSelf:
    """Minimal ``self`` for calling the mixin's read methods unbound."""

    def __init__(self):
        self.calls = []
        self.crypto = Mock()
        self._db = self

    def fetch_all(self, sql, params=()):
        self.calls.append((sql, params))
        return []


@pytest.mark.parametrize("method_name", ["get_login_history", "get_security_events"])
@pytest.mark.parametrize("bad_limit", [-1, -100, 10**9, "abc", float("nan"), True])
def test_audit_history_rejects_unbounded_or_malformed_limit(method_name, bad_limit):
    stub = _AuditSelf()
    method = getattr(AuditMixin, method_name)

    with pytest.raises(HardeningViolation):
        method(stub, 1, limit=bad_limit)

    assert stub.calls == []


@pytest.mark.parametrize("method_name", ["get_login_history", "get_security_events"])
def test_audit_history_valid_limit_still_forwarded(method_name):
    stub = _AuditSelf()
    method = getattr(AuditMixin, method_name)

    assert method(stub, 1, limit=25) == []

    assert len(stub.calls) == 1
    assert stub.calls[0][1][-1] == 25


def test_audit_page_cap_constant_is_bounded():
    assert 0 < _MAX_AUDIT_PAGE <= 1000
