"""A demoted admin's surviving session must not grant migration-data authority.

Role edits/revocations deliberately leave rows in ``admin_sessions`` intact:
admin routes re-read roles on every request, so they never need to revoke a
session to make a permission change effective. The three migration *read*
routes (``/migrations``, ``/migrations/status``, ``/migrations/{version}``)
authenticated with ``get_admin_user`` -- which only validates the admin session
via ``get_admin_from_token`` -- and then exposed migration metadata, status and
execution logs. A demoted admin whose roles were all revoked kept that session
row and kept reading all of it until natural expiry.

``get_admin_user`` now re-checks that the session still carries a live granted
scope via ``_admin_has_live_scope`` before authorising those reads. These tests
pin the helper's fail-closed behaviour and the fact that the dependency routes
through it instead of trusting session truthiness alone. The helper is loaded
straight from the module source so the tests exercise the real shipped code
without importing the API app bootstrap.
"""

import ast
import json
import sys
import types
from pathlib import Path

import pytest


pytestmark = [pytest.mark.security, pytest.mark.unit]

ADMIN_ID = 4242
_MIGRATIONS = Path(__file__).parents[2] / "api" / "routes" / "admin" / "migrations.py"
_SOURCE = _MIGRATIONS.read_text(encoding="utf-8")


class _DB:
    def __init__(self, rows, error=None):
        self.rows = rows
        self.error = error

    def fetch_all(self, sql, params=()):
        if self.error:
            raise self.error
        return self.rows


def _load(fn_name: str):
    """Compile a single function definition out of the real module source."""
    tree = ast.parse(_SOURCE)
    node = next(
        n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name
    )
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {"json": json}
    exec(compile(module, str(_MIGRATIONS), "exec"), namespace)
    return namespace[fn_name]


_SENTINEL = object()


@pytest.fixture
def live_scope(monkeypatch):
    helper = _load("_admin_has_live_scope")

    def call(rows, error=None, db=_SENTINEL):
        resolved = _DB(rows, error=error) if db is _SENTINEL else db
        fake_api = types.ModuleType("src.api")
        fake_api.get_db = lambda: resolved
        monkeypatch.setitem(sys.modules, "src.api", fake_api)
        return helper(ADMIN_ID)

    return call


def test_demoted_admin_with_no_roles_is_not_authoritative(live_scope):
    assert live_scope([]) is False


def test_admin_with_all_scopes_revoked_is_not_authoritative(live_scope):
    rows = [{"permissions": '{"admin.migrations.execute": false, "*": 0}'}]
    assert live_scope(rows) is False


def test_malformed_role_payload_is_not_authoritative(live_scope):
    assert live_scope([{"permissions": "not-json"}]) is False
    assert live_scope([{"permissions": [1, 2, 3]}]) is False


def test_missing_db_fails_closed(live_scope):
    assert live_scope([], db=None) is False


def test_database_error_fails_closed(live_scope):
    assert live_scope([], error=RuntimeError("db down")) is False


def test_scoped_admin_is_still_authoritative(live_scope):
    rows = [{"permissions": '{"admin.migrations.execute": true}'}]
    assert live_scope(rows) is True


def test_get_admin_user_rechecks_live_authority():
    """Ratchet: the dependency must re-check live roles, not just session truth."""
    tree = ast.parse(_SOURCE)
    node = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "get_admin_user"
    )
    body = ast.get_source_segment(_SOURCE, node)
    assert "_admin_has_live_scope(" in body
    assert "get_admin_from_token(" in body
