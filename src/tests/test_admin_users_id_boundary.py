"""Admin-users route ids must go through the shared ASCII snowflake gate.

The admin-users family binds ``/users/{user_id}`` and ``/admin-users/{user_id}``
path segments as raw ``str`` and funnels all 16 handlers through one shared
helper, ``AdminUsersRouterBase._parse_user_id``. That helper used to call bare
``int(user_id)``, which accepts Unicode decimal homoglyphs (``"٤٢"``/``"４２"``
-> 42), PEP-515 separators (``"1_2"`` -> 12), signs/whitespace and
arbitrary-precision or negative values. The normalized id then reached admin
lookups, storage-quota overrides, role/status mutations and cache keys even
though every other route boundary parses snowflakes through ``parse_id``. A
token one hop renders as text was silently reinterpreted as a different id
here -- a real parser differential.

This suite locks the boundary with both a functional test (hostile ids are
rejected with 400 before any domain call) and an AST ratchet over the helper
so the bare ``int()`` cannot creep back in. No database/network/app fixtures.
"""

import ast
import asyncio
import textwrap
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.api.routes.admin.users.base import AdminUsersRouterBase
from src.core.security.hardening import SNOWFLAKE_MAX

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "src" / "api" / "routes" / "admin" / "users" / "base.py"
FAMILY = (
    "management.py",
    "lifecycle.py",
    "admin_users.py",
)

# Accepted by bare int() (or, for the digit cap, raising an uncaught bare
# ValueError) but rejected by the ASCII snowflake gate.
HOSTILE_IDS = [
    "0",  # not a real snowflake
    "-1",  # negative
    str(SNOWFLAKE_MAX + 1),  # just past the signed 64-bit ceiling
    str(2**80),  # far past it
    "9" * 5000,  # must fail validation, not int()'s digit cap
    "1_2",  # PEP 515 underscore -> int 12
    "٤٢",  # Arabic-Indic digits -> int 42
    "４２",  # fullwidth digits -> int 42
    "1.5",  # fraction
    "0x10",  # non-decimal prefix
    "abc",
    "",
    None,
]

# Canonical grammar kept by bounded_int(): surrounding whitespace, an
# optional leading '+' and leading zeros still parse to the same id.
CANONICAL_IDS = {
    "1": 1,
    "42": 42,
    str(SNOWFLAKE_MAX): SNOWFLAKE_MAX,
    "  123  ": 123,
    "+7": 7,
    "007": 7,
}


# ---------------------------------------------------------------------------
# functional boundary tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", HOSTILE_IDS)
def test_parse_user_id_rejects_hostile_id(raw):
    with pytest.raises(HTTPException) as exc:
        AdminUsersRouterBase()._parse_user_id(raw)
    assert exc.value.status_code == 400
    assert exc.value.detail == {"error": {"code": 400, "message": "Invalid user ID"}}


@pytest.mark.parametrize("raw,expected", list(CANONICAL_IDS.items()))
def test_parse_user_id_accepts_canonical_id(raw, expected):
    assert AdminUsersRouterBase()._parse_user_id(raw) == expected


def test_get_user_details_rejects_hostile_id_before_domain_call(monkeypatch):
    """A hostile path segment never reaches the admin lookup."""
    from src.api.routes.admin.users import management

    monkeypatch.setattr(management, "check_host_restriction", lambda request: None)
    monkeypatch.setattr(
        management, "require_admin_permission", lambda request, scope: 1
    )

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("domain lookup called with an unvalidated id")

    monkeypatch.setattr(
        "src.core.admin.get_user_details", _must_not_be_called, raising=False
    )

    from src.api.routes.admin.users.management import ManagementMixin

    handler = type("_Router", (ManagementMixin, AdminUsersRouterBase), {})()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(handler.get_user_details(request=object(), user_id="٤٢"))
    assert exc.value.status_code == 400


# ---------------------------------------------------------------------------
# AST ratchet: the helper must use the shared gate, not bare int()
# ---------------------------------------------------------------------------


def _find_class(tree, name):
    return next(
        (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == name),
        None,
    )


def _methods(cls):
    return {
        n.name: n
        for n in ast.walk(cls)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _uses_parse_id(function) -> bool:
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == "parse_id")
            or (isinstance(node.func, ast.Attribute) and node.func.attr == "parse_id")
        )
        for node in ast.walk(function)
    )


def _uses_bare_int(function) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "int"
        for node in ast.walk(function)
    )


_TREE = ast.parse(BASE.read_text(encoding="utf-8"))
_CLASS = _find_class(_TREE, "AdminUsersRouterBase")
_HELPER = _methods(_CLASS).get("_parse_user_id") if _CLASS is not None else None


def test_ast_detector_distinguishes_old_and_new_helper():
    """Self-test: the detector flags the pre-fix helper, not the hardened one."""
    old = _methods(
        _find_class(
            ast.parse(
                textwrap.dedent("""
        class M:
            def _parse_user_id(self, user_id):
                try:
                    return int(user_id)
                except ValueError:
                    raise HTTPException(status_code=400)
    """)
            ),
            "M",
        )
    )["_parse_user_id"]
    new = _methods(
        _find_class(
            ast.parse(
                textwrap.dedent("""
        class M:
            def _parse_user_id(self, user_id):
                try:
                    return parse_id(user_id, "user_id")
                except HardeningViolation:
                    raise HTTPException(status_code=400)
    """)
            ),
            "M",
        )
    )["_parse_user_id"]
    assert _uses_bare_int(old) and not _uses_parse_id(old)
    assert not _uses_bare_int(new) and _uses_parse_id(new)


def test_helper_uses_shared_gate_and_no_bare_int():
    assert _CLASS is not None, "AdminUsersRouterBase disappeared"
    assert _HELPER is not None, "_parse_user_id disappeared"
    assert _uses_parse_id(_HELPER), "helper no longer calls parse_id"
    assert not _uses_bare_int(_HELPER), "bare int() crept back into the id boundary"


@pytest.mark.parametrize("filename", FAMILY)
def test_family_handlers_route_through_the_helper(filename):
    source = (BASE.parent / filename).read_text(encoding="utf-8")
    assert "self._parse_user_id(user_id)" in source, filename
    assert "int(user_id)" not in source, filename
