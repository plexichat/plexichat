"""Server-creation premium license gate must fail closed.

``create_server`` enforces the licensed ``max_servers`` ceiling (falling back
to the configured ``max_servers_per_user`` cap). Previously an uninitialised
license manager or a missing licensing module raised inside that gate and was
swallowed by ``except (RuntimeError, ImportError): pass``, which dropped *all*
server-count enforcement and let a single owner create unlimited servers --
the same fail-open class as the bots-license gate, in a different gate.

These tests prove the gate still denies when the license cannot be evaluated,
and that the licensed/free-tier verdicts are unchanged.
"""

import sys
from unittest.mock import patch

import pytest

import utils.licensing as licensing
from src.core.servers.exceptions import ServerLimitReachedError
from src.core.servers.manager.server_ops import ServerOpsMixin

pytestmark = [pytest.mark.unit, pytest.mark.security]


class _DBStub:
    """Only the count query and the (must-not-run) insert are modelled."""

    def __init__(self, owned: int):
        self.owned = owned
        self.executed = []

    def fetch_one(self, sql, params):
        assert "COUNT(*)" in sql
        return {"c": self.owned}

    def execute(self, sql, params):
        self.executed.append((sql, params))


class _Host(ServerOpsMixin):
    def __init__(self, owned: int, max_per_user: int = 1):
        self._config = {"max_servers_per_user": max_per_user}
        self._db = _DBStub(owned)


def _license_unavailable(monkeypatch):
    """Make ``from utils.licensing import ...`` raise RuntimeError mid-gate."""

    def _boom(*args, **kwargs):
        raise RuntimeError("licensing not configured")

    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "get_license_limit", _boom)


def test_server_creation_denies_when_license_check_raises(monkeypatch):
    _license_unavailable(monkeypatch)
    host = _Host(owned=1)  # already at the configured cap of 1

    with pytest.raises(ServerLimitReachedError):
        host.create_server(owner_id=7, name="Yet another server")

    # The fail-closed verdict must stop creation before any write.
    assert host._db.executed == []


def test_server_creation_denies_when_licensing_module_is_unavailable():
    host = _Host(owned=1)
    # A None entry in sys.modules makes the import in the gate raise ImportError.
    with patch.dict(sys.modules, {"utils.licensing": None}):
        with pytest.raises(ServerLimitReachedError):
            host.create_server(owner_id=7, name="Yet another server")

    assert host._db.executed == []


def test_finite_license_cap_is_still_enforced(monkeypatch):
    """A healthy, licensed gate keeps enforcing the license's own cap."""
    monkeypatch.setattr(licensing, "is_free_tier", lambda: False)
    monkeypatch.setattr(licensing, "get_license_limit", lambda *a, **k: 1)

    host = _Host(owned=1, max_per_user=100)
    with pytest.raises(ServerLimitReachedError):
        host.create_server(owner_id=7, name="Over the license cap")

    assert host._db.executed == []
