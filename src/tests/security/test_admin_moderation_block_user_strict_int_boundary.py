"""Admin ``block_user`` must bind the target id with a strict-int gate.

``POST /admin/roles``-style laundering also sat on the platform-wide ban path:
``block_user`` converted the caller-supplied ``block_request.user_id`` with a
bare ``int()``:

    target_id = int(block_request.user_id)
    _ensure_can_target_admin(admin_id, target_id)
    admin.block_user(target_id, ...)

``int()`` launders malformed-but-coercible tokens into a *different* target:
``True``->1, ``False``->0, ``5.9``->5, ``"٤٢"``->42, ``"1_2"``->12. The
``SnowflakeID`` schema rejects those on the normal HTTP path, but a directly
constructed body (or an internal caller) bypasses that validation; the
laundered id then reached both the admin-hierarchy gate and the ban write.
Every other admin id-boundary funnels through ``parse_id``; this handler was
the lone exception. The fix routes the field through ``parse_id`` and fails
closed with 400 before any gate or write.

The behavioral test is non-vacuous: a live mock records the exact
``admin.block_user`` call, so the positive control proves the harness reaches
the write and every hostile id must be refused with *no* ban. An AST ratchet
stops bare ``int()`` from creeping back, with a synthetic detector self-test.
A committed-tree guard pins the fix in HEAD so a whole-file revert cannot
silently remove it.
"""

from __future__ import annotations

import ast
import asyncio
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException, Request

from src.api.routes.admin import moderation
from src.core import admin as admin_core

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODERATION_SRC = _REPO_ROOT / "src" / "api" / "routes" / "admin" / "moderation.py"

HOSTILE_IDS = [
    True,  # bool -> 1
    False,  # bool -> 0
    5.9,  # float truncates to 5
    "٤٢",  # Arabic-Indic homoglyph -> 42
    "1_2",  # PEP 515 separator -> 12
]


def _req():
    return cast(Request, SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))


def _body(user_id):
    # Directly constructed body: bypasses Pydantic SnowflakeID validation, the
    # exact scenario that made the old bare int() a laundering boundary.
    return SimpleNamespace(user_id=user_id, reason="r", duration_hours=None)


def _patch(monkeypatch, ban_calls, actor=10):
    monkeypatch.setattr(moderation, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(moderation, "require_admin_permission", lambda r, p: actor)
    monkeypatch.setattr(moderation, "_ensure_can_target_admin", lambda a, t: None)

    def _record(target, *args, **kwargs):
        ban_calls.append(target)
        return True

    monkeypatch.setattr(admin_core, "block_user", _record)


def test_harness_reaches_the_ban_write(monkeypatch):
    """Non-vacuity control: a genuine id produces exactly one ban."""
    calls = []
    _patch(monkeypatch, calls)
    result = asyncio.run(moderation.block_user(_body("42"), _req()))
    assert result.success is True
    assert calls == [42]


@pytest.mark.parametrize("bad", HOSTILE_IDS, ids=repr)
def test_hostile_id_rejected_before_any_ban_write(monkeypatch, bad):
    calls = []
    _patch(monkeypatch, calls)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(moderation.block_user(_body(bad), _req()))
    assert exc.value.status_code == 400
    assert calls == [], "a laundered id reached the ban write"


# ---------------------------------------------------------------------------
# AST ratchet: block_user binds the target id through parse_id, never int().
# ---------------------------------------------------------------------------


def _block_user_node(tree):
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "block_user"
    )


def _assignment_sources(node, name):
    """Return source text of the RHS for assignments whose call is ``name``."""
    found = []
    for stmt in ast.walk(node):
        if not isinstance(stmt, ast.Assign) or not isinstance(stmt.value, ast.Call):
            continue
        fn = stmt.value.func
        called = (isinstance(fn, ast.Name) and fn.id == name) or (
            isinstance(fn, ast.Attribute) and fn.attr == name
        )
        if called:
            found.append(ast.unparse(stmt.value))
    return found


def _detector_flags_bare_int():
    old = textwrap.dedent(
        """
        async def block_user(block_request, request):
            target_id = int(block_request.user_id)
        """
    )
    return _assignment_sources(_block_user_node(ast.parse(old)), "int")


def test_detector_is_not_vacuous():
    assert any("block_request.user_id" in src for src in _detector_flags_bare_int())


def test_block_user_binds_target_id_through_parse_id():
    tree = ast.parse(_MODERATION_SRC.read_text(encoding="utf-8"))
    node = _block_user_node(tree)
    assert not _assignment_sources(node, "int"), "bare int() reintroduced in block_user"
    assert any(
        "parse_id" in src and "block_request.user_id" in src
        for src in _assignment_sources(node, "parse_id")
    )


# ---------------------------------------------------------------------------
# Committed-tree guard: the fix is pinned in HEAD, not just the working tree.
# ---------------------------------------------------------------------------


def _git_show_head(path: str) -> str:
    proc = subprocess.run(
        ["git", "show", f"HEAD:{path}"],
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.returncode == 0, proc.stderr.strip()
    return proc.stdout


def test_committed_block_user_gate_is_present():
    src = _git_show_head("src/api/routes/admin/moderation.py")
    node = _block_user_node(ast.parse(src))
    assert not _assignment_sources(node, "int")
    assert any(
        "parse_id" in src2 and "block_request.user_id" in src2
        for src2 in _assignment_sources(node, "parse_id")
    )
