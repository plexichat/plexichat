"""Regression: uninstalling an application enforces object-level authorization.

The public ``uninstall_application`` core API used to accept a caller
``user_id`` and ignore it entirely, so any internal caller could tear down an
installation on a server it neither installed onto nor manages. These tests
pin the owner-or-server.manage gate and its fail-closed behaviour without
requiring a database fixture.
"""

import pytest

from src.core.applications.exceptions import PermissionDeniedError
from src.core.applications.manager.installations import InstallationMixin

APP_ID = 1234
SERVER_ID = 5678
INSTALLER_ID = 9100
STRANGER_ID = 9200


class _FakeDB:
    def __init__(self, installer_id):
        self.installer_id = installer_id
        self.deletes = []

    def fetch_one(self, query, params=()):
        if "installer_id" not in query:
            raise AssertionError("uninstall must read the installer_id column")
        app_id, server_id = params
        if (app_id, server_id) == (APP_ID, SERVER_ID):
            return {"id": 1, "installer_id": self.installer_id}
        return None

    def execute(self, query, params=()):
        self.deletes.append((query, params))
        return _Cursor()

    def fetch_all(self, query, params=()):
        return []


class _Cursor:
    rowcount = 1


class _ServersStub:
    def __init__(self, allowed):
        self.allowed = allowed
        self.checked = []

    def has_permission(self, user_id, server_id, permission):
        self.checked.append((user_id, server_id, permission))
        assert permission == "server.manage"
        return self.allowed


def _manager(installer_id=INSTALLER_ID, servers=None):
    mgr = object.__new__(InstallationMixin)
    mgr._db = _FakeDB(installer_id)
    mgr._servers = servers
    mgr.get_application = lambda _app_id: None
    return mgr


def test_non_installer_without_permission_is_denied():
    mgr = _manager(servers=_ServersStub(allowed=False))
    with pytest.raises(PermissionDeniedError):
        mgr.uninstall_application(APP_ID, SERVER_ID, STRANGER_ID)
    assert mgr._db.deletes == [], "a denied uninstall must not issue a DELETE"


def test_uninstall_fails_closed_without_servers_module():
    mgr = _manager(servers=None)
    with pytest.raises(PermissionDeniedError):
        mgr.uninstall_application(APP_ID, SERVER_ID, STRANGER_ID)
    assert mgr._db.deletes == []


def test_server_manage_allows_uninstall_by_non_installer():
    servers = _ServersStub(allowed=True)
    mgr = _manager(servers=servers)
    assert mgr.uninstall_application(APP_ID, SERVER_ID, STRANGER_ID) is True
    assert len(mgr._db.deletes) == 1
    assert servers.checked == [(STRANGER_ID, SERVER_ID, "server.manage")]


def test_original_installer_may_uninstall_without_servers_module():
    mgr = _manager(servers=None)
    assert mgr.uninstall_application(APP_ID, SERVER_ID, INSTALLER_ID) is True
    assert len(mgr._db.deletes) == 1
