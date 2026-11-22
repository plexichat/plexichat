"""Admin artifact route ids must go through the ASCII snowflake gate.

``src/api/routes/admin/artifacts.py`` binds three attacker-controlled ids with
a bare ``int()``:

* ``admin_delete_artifact`` -- the ``/artifacts/{artifact_id}`` *path* segment
  of a destructive, cross-server force-delete;
* ``admin_set_feature_settings`` -- ``server_id`` from a free-form ``Dict``
  body (no Pydantic snowflake validation at all);
* ``admin_set_server_retention`` -- ``body.server_id`` (schema-vetted in normal
  FastAPI traffic, but still parsed again here).

``int(str)`` accepts Unicode decimal homoglyphs ("٤٢" -> 42), PEP-515
separators ("1_2" -> 12), signs/whitespace, negatives, booleans (via direct
calls), fractions (truncation) and arbitrary-precision integers. A segment one
hop reads as text was therefore reinterpreted as a different id at an
admin-only endpoint, and every other admin route family already parses ids
through ``parse_id``.

This suite locks the boundary with functional tests (hostile ids rejected with
400 before any domain call) and an AST ratchet over the three handlers so a
bare ``int()`` cannot creep back in. No database/network/app fixtures.
"""

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes.admin import artifacts as artifacts_routes
from src.core.security.hardening import SNOWFLAKE_MAX

pytestmark = [pytest.mark.unit, pytest.mark.security]

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "src" / "api" / "routes" / "admin" / "artifacts.py"

HOSTILE_IDS = [
    "0",  # not a real snowflake
    "-1",  # negative
    str(SNOWFLAKE_MAX + 1),  # just past the signed 64-bit ceiling
    str(2**80),  # far past it
    "9" * 5000,  # must fail validation, not int()'s digit cap
    "1_2",  # PEP 515 underscore -> int 12
    "٤٢",  # Arabic-Indic digits -> int 42
    "４２",  # fullwidth digits -> int 42
    "1.5",  # fraction -> int() truncates to 1
    "0x10",  # non-decimal prefix
    "abc",
    "",
    None,
]

CANONICAL_IDS = {
    "1": 1,
    "42": 42,
    str(SNOWFLAKE_MAX): SNOWFLAKE_MAX,
    "  123  ": 123,
    "+7": 7,
    "007": 7,
}


def _patch_admin_boundary(monkeypatch):
    """Bypass auth/host checks; make any domain access fail loudly."""
    monkeypatch.setattr(
        artifacts_routes, "check_host_restriction", lambda request: None
    )
    monkeypatch.setattr(
        artifacts_routes, "require_admin_permission", lambda request, scope: 1
    )
    monkeypatch.setattr(
        artifacts_routes, "require_approved_request", lambda scope: None
    )

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError("domain call made with an unvalidated id")

    monkeypatch.setattr(artifacts_routes, "_get_manager", _must_not_be_called)


def _assert_400(exc, message):
    assert exc.value.status_code == 400
    assert exc.value.detail == {"error": {"code": 400, "message": message}}


# ---------------------------------------------------------------------------
# functional boundary tests
# ---------------------------------------------------------------------------


def test_delete_artifact_hostile_ids_are_400(monkeypatch):
    _patch_admin_boundary(monkeypatch)
    for raw in HOSTILE_IDS:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(artifacts_routes.admin_delete_artifact(str(raw), object()))
        _assert_400(exc, "Invalid artifact ID")


@pytest.mark.parametrize("raw", [value for value in HOSTILE_IDS if value is not None])
def test_set_feature_settings_rejects_hostile_server_id(monkeypatch, raw):
    _patch_admin_boundary(monkeypatch)
    body = {"server_id": raw, "settings": {"k": "v"}}
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            artifacts_routes.admin_set_feature_settings("plexiscribe", body, object())
        )
    _assert_400(exc, "Invalid server_id")


@pytest.mark.parametrize("raw", HOSTILE_IDS)
def test_set_server_retention_rejects_hostile_server_id(monkeypatch, raw):
    _patch_admin_boundary(monkeypatch)
    body = SimpleNamespace(server_id=raw, retention_days=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(artifacts_routes.admin_set_server_retention(object(), body))
    _assert_400(exc, "Invalid server_id")


def test_bool_and_float_server_ids_are_rejected(monkeypatch):
    """Direct model/body construction must not coerce bool/float into an id."""
    _patch_admin_boundary(monkeypatch)
    for raw in (True, False, 1.9, -0.5):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                artifacts_routes.admin_set_feature_settings(
                    "plexiscribe", {"server_id": raw, "settings": {}}, object()
                )
            )
        _assert_400(exc, "Invalid server_id")


@pytest.mark.parametrize("raw,expected", list(CANONICAL_IDS.items()))
def test_parse_id_accepts_canonical_ids(raw, expected):
    from src.core.security.hardening import parse_id

    assert parse_id(raw, "artifact_id") == expected


# ---------------------------------------------------------------------------
# AST ratchet: the handlers must use the shared gate, not bare int()
# ---------------------------------------------------------------------------


def _find_function(tree, name):
    return next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name
        ),
        None,
    )


def _calls(func, name):
    return any(
        isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
        for node in ast.walk(func)
    )


@pytest.mark.parametrize(
    "handler",
    [
        "admin_delete_artifact",
        "admin_set_feature_settings",
        "admin_set_server_retention",
    ],
)
def test_handler_uses_shared_gate_and_no_bare_int(handler):
    tree = ast.parse(MODULE.read_text(encoding="utf-8"))
    func = _find_function(tree, handler)
    assert func is not None, f"{handler} disappeared"
    assert _calls(func, "parse_id"), f"{handler} no longer calls parse_id"
    assert not _calls(func, "int"), f"bare int() crept back into {handler}"
