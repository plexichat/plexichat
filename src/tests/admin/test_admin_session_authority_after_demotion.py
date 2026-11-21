"""A demoted admin's surviving session must not grant media-access authority.

Role edits/revocations deliberately leave rows in ``admin_sessions`` intact:
admin routes re-read roles on every request, so they do not need to revoke a
session to make permission changes effective. The attachment-serving route,
however, used a truthy ``admin.validate_session(token)`` as a *boolean* admin
grant to bypass the per-file access check and to authorise any attachment with
an admin bearer token. An admin whose roles were all revoked -- or whose
scopes were all switched off -- kept its session row and kept that bypass
until natural expiry: stale admin authority after a demotion.

The route now requires at least one still-granted scope from a live role
assignment (``_admin_session_is_authoritative``) before trusting a bearer as
an admin. These tests pin the helper's fail-closed behaviour and the fact that
the three boolean-grant call sites route through it instead of raw session
truthiness.
"""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

import src.api.media_serving as media_serving
from src.api.media_serving import _admin_session_is_authoritative


pytestmark = [pytest.mark.security, pytest.mark.unit]

ADMIN_ID = 4242


class _AdminModule:
    """Admin module double: a session is "valid" but roles may be gone."""

    def __init__(self, admin_id=ADMIN_ID):
        self.admin_id = admin_id

    def validate_session(self, token):
        return self.admin_id if token == "admin-token" else None


class _DB:
    def __init__(self, rows, error=None):
        self.rows = rows
        self.error = error

    def fetch_all(self, sql, params=()):
        if self.error:
            raise self.error
        return self.rows


def _authoritative(rows, *, error=None):
    with patch("src.api.get_db", return_value=_DB(rows, error=error)):
        return _admin_session_is_authoritative("admin-token", _AdminModule())


def test_demoted_admin_with_no_roles_is_not_authoritative():
    # Session row still matches, but every role was revoked: no assignments.
    assert _authoritative([]) is False


def test_admin_with_all_scopes_revoked_is_not_authoritative():
    # A role remains but every scope was flipped to false / non-boolean.
    rows = [{"permissions": '{"admin.read": false, "*": 0, "x": "true"}'}]
    assert _authoritative(rows) is False


def test_malformed_role_payload_is_not_authoritative():
    assert _authoritative([{"permissions": "not-json"}]) is False
    assert _authoritative([{"permissions": [1, 2, 3]}]) is False


def test_invalid_session_is_not_authoritative():
    with patch("src.api.get_db", return_value=_DB([])):
        assert _admin_session_is_authoritative("wrong-token", _AdminModule()) is False


def test_database_error_fails_closed():
    assert _authoritative([], error=RuntimeError("db down")) is False


def test_scoped_admin_is_still_authoritative():
    # Control: a live role granting one scope keeps admin authority.
    assert _authoritative([{"permissions": '{"admin.read": true}'}]) is True


def _serve_attachment_source():
    """Source of the nested route handler, located via its fixed path."""
    tree = ast.parse(Path(media_serving.__file__).read_text(encoding="utf-8"))

    def _find(node, name):
        for child in ast.walk(node):
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name == name
            ):
                return child
        return None

    fn = _find(tree, "_admin_session_is_authoritative")
    assert fn is not None
    return ast.get_source_segment(
        Path(media_serving.__file__).read_text(encoding="utf-8"), fn
    )


def test_route_boolean_grants_go_through_authority_helper():
    """Guard against reintroducing a raw validate_session truthiness check.

    Before the fix the route used ``bool(admin.validate_session(token))`` at
    three points; a demoted admin's surviving session therefore bypassed the
    per-file access check. This source ratchet fails if that pattern returns.
    """
    source = Path(media_serving.__file__).read_text(encoding="utf-8")
    assert source.count("_admin_session_is_authoritative(") >= 4  # 3 uses + def
    for raw_check in (
        "bool(admin_mod.validate_session(token))",
        "and admin.validate_session(token)",
        "and admin_mod.validate_session(token)",
    ):
        assert raw_check not in source, (
            f"raw admin-session boolean grant returned: {raw_check}"
        )
