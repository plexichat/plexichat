"""Admin ``create_automod_rule`` must bind the server id with a strict-int gate.

``create_automod_rule`` converted the caller-supplied ``body.server_id`` with a
bare ``int()``:

    rule = automod.create_rule(
        user_id=int(admin_id),
        server_id=int(body.server_id),
        ...
    )

``int()`` launders malformed-but-coercible tokens into a *different* server:
``True``->1, ``False``->0, ``5.9``->5, ``"٤٢"``->42, ``"1_2"``->12. The
``SnowflakeID`` schema in ``AutomodRuleCreateRequest`` rejects those on the
normal HTTP path, but a directly constructed body (or an internal caller)
bypasses Pydantic validation; the laundered id then became the server binding
of a newly written AutoMod rule. Every other admin id-boundary funnels through
``parse_id``; this write was the remaining exception. The fix routes the field
through ``parse_id`` and fails closed with 400 before any rule write.

The behavioral test is non-vacuous: a live mock records the exact
``automod.create_rule`` call, so the positive control proves the harness reaches
the write and every hostile id must be refused with *no* rule created. An AST
ratchet stops bare ``int()`` from creeping back, with a synthetic detector
self-test. A committed-tree guard pins the fix in HEAD so a whole-file revert
cannot silently remove it.
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
from src.core import automod as automod_core

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MODERATION_SRC = _REPO_ROOT / "src" / "api" / "routes" / "admin" / "moderation.py"

HOSTILE_IDS = [
    True,  # bool -> 1
    False,  # bool -> 0
    5.9,  # float truncates to 5
    "٤٢",  # Arabic-Indic homoglyph -> 42
    "1_2",  # PEP 515 separator -> 12
    -1,  # negative -> out of snowflake range
    99000000000000000000,  # >64-bit -> out of snowflake range
]


def _req():
    return cast(Request, SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")))


def _body(server_id):
    # Directly constructed body: bypasses Pydantic SnowflakeID validation, the
    # exact scenario that made the old bare int() a laundering boundary.
    return SimpleNamespace(
        server_id=server_id,
        rule_type="keyword",
        name="r",
        config={},
        actions=[],
        exempt_roles=None,
        exempt_channels=None,
        priority=0,
        check_all=False,
        enabled=True,
    )


def _patch(monkeypatch, create_calls, actor=10):
    monkeypatch.setattr(moderation, "check_host_restriction", lambda r: None)
    monkeypatch.setattr(moderation, "require_admin_permission", lambda r, p: actor)

    def _record(**kwargs):
        create_calls.append(kwargs["server_id"])
        return SimpleNamespace(id=1)

    monkeypatch.setattr(automod_core, "create_rule", _record)
    # Return a sentinel from the response mapper so the positive control only
    # depends on whether the protected write was reached.
    monkeypatch.setattr(moderation, "_rule_to_response", lambda rule: "created")


def test_harness_reaches_the_rule_write(monkeypatch):
    """Non-vacuity control: a genuine id creates exactly one rule."""
    calls = []
    _patch(monkeypatch, calls)
    result = asyncio.run(moderation.create_automod_rule(_body("42"), _req()))
    assert result == "created"
    assert calls == [42]


@pytest.mark.parametrize("bad", HOSTILE_IDS, ids=repr)
def test_hostile_id_rejected_before_any_rule_write(monkeypatch, bad):
    calls = []
    _patch(monkeypatch, calls)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(moderation.create_automod_rule(_body(bad), _req()))
    assert exc.value.status_code == 400
    assert calls == [], "a laundered id reached the rule write"


# ---------------------------------------------------------------------------
# AST ratchet: create_automod_rule binds server_id through parse_id, never int().
# ---------------------------------------------------------------------------


def _create_automod_rule_node(tree):
    return next(
        n
        for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name == "create_automod_rule"
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


def _call_argument_sources(node, name):
    """Return source text of every call to ``name`` in the node."""
    return [
        ast.unparse(n)
        for n in ast.walk(node)
        if isinstance(n, ast.Call)
        and (
            (isinstance(n.func, ast.Name) and n.func.id == name)
            or (isinstance(n.func, ast.Attribute) and n.func.attr == name)
        )
    ]


def _detector_flags_bare_int():
    old = textwrap.dedent(
        """
        async def create_automod_rule(body, request):
            rule = automod.create_rule(server_id=int(body.server_id))
        """
    )
    return _call_argument_sources(_create_automod_rule_node(ast.parse(old)), "create_rule")


def test_detector_is_not_vacuous():
    assert any("body.server_id" in src for src in _detector_flags_bare_int())


def test_create_automod_rule_binds_server_id_through_parse_id():
    tree = ast.parse(_MODERATION_SRC.read_text(encoding="utf-8"))
    node = _create_automod_rule_node(tree)
    assert not any(
        "body.server_id" in src for src in _call_argument_sources(node, "create_rule")
    ), "bare int() reintroduced on the create_rule server_id binding"
    assert any(
        "parse_id" in src and "body.server_id" in src
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


def test_committed_create_automod_rule_gate_is_present():
    src = _git_show_head("src/api/routes/admin/moderation.py")
    node = _create_automod_rule_node(ast.parse(src))
    assert not any(
        "body.server_id" in src2 for src2 in _call_argument_sources(node, "create_rule")
    )
    assert any(
        "parse_id" in src2 and "body.server_id" in src2
        for src2 in _assignment_sources(node, "parse_id")
    )
