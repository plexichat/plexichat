"""Committed-tree pin: add_reaction must route through the fail-closed view gate.

The add path previously used an inline ``if servers_mod and hasattr(...)`` gate
that SKIPPED when the servers module was unwired -- a fail-open that let a user
react to a private-channel message without the channel-view check. The fix
routes add through the canonical ``_require_channel_view`` helper (which denies
when servers is unwired), mirroring the removal/clear paths.

This workspace has peers staging stale whole-file copies, so pin the invariant
against the COMMITTED blob (``git show HEAD:...``) rather than the working tree:
a future staged revert cannot silently reintroduce the fail-open. This is a
standalone guard on purpose -- the shared security-marker registry is contested
by other agents, and a self-contained pin cannot be clobbered by their edits.

Non-vacuity: a synthetic add_reaction with the old inline gate and no helper
call is flagged, and stripping the helper call from the real committed blob
makes the guard fail. The reader also proves it saw a real blob containing the
``add_reaction`` function, so the guard is not passing on empty input.
"""

from __future__ import annotations

import ast
import subprocess
from functools import lru_cache
from pathlib import Path

pytestmark = ["unit", "security"]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REL = "src/api/routes/reactions.py"


def _git(*args: str) -> str:
    return subprocess.check_output(
        ["git", *args], cwd=_REPO_ROOT, stderr=subprocess.DEVNULL
    ).decode("utf-8")


@lru_cache(maxsize=1)
def _head() -> str:
    return _git("rev-parse", "HEAD").strip()


@lru_cache(maxsize=1)
def _committed_source() -> str:
    return _git("show", f"{_head()}:{_REL}")


def _name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name(node.func)
    return None


def _function_source(source: str, name: str) -> str:
    """Return the source segment of the named (async) function, or ''."""
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            segment = ast.get_source_segment(source, node)
            if segment:
                return segment
    return ""


def _classify(add_source: str) -> tuple[bool, bool]:
    """Return ``(calls_fail_closed_helper, has_fail_open_inline_gate)``."""
    tree = ast.parse(add_source)
    calls_helper = False
    inline_gate = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _name(node.func) == "_require_channel_view":
            calls_helper = True
        if isinstance(node, ast.If) and isinstance(node.test, ast.BoolOp):
            if isinstance(node.test.op, ast.And):
                parts = node.test.values
                has_mod = any(_name(p) == "servers_mod" for p in parts)
                has_hasattr = any(
                    isinstance(p, ast.Call)
                    and _name(p.func) == "hasattr"
                    and p.args
                    and _name(p.args[0]) == "servers_mod"
                    for p in parts
                )
                if has_mod and has_hasattr:
                    inline_gate = True
    return calls_helper, inline_gate


def test_committed_add_reaction_is_fail_closed():
    add_source = _function_source(_committed_source(), "add_reaction")
    assert add_source, "guard went blind: add_reaction missing from committed blob"
    calls_helper, inline_gate = _classify(add_source)
    assert calls_helper, (
        "committed add_reaction does not call _require_channel_view; an unwired "
        "servers module would skip the channel-view gate (fail-open)"
    )
    assert not inline_gate, (
        "committed add_reaction reintroduced the inline "
        "`if servers_mod and hasattr(...)` gate that skips when unwired"
    )


def test_guard_is_not_vacuous():
    # A synthetic vulnerable add_reaction is detected by both halves of the pin.
    vulnerable = """async def add_reaction():
    servers_mod = api.get_servers()
    if servers_mod and hasattr(servers_mod, "get_channel_by_conversation"):
        conv_id = reactions.get_conversation_id_from_message(mid)
"""
    calls_helper, inline_gate = _classify(vulnerable)
    assert inline_gate and not calls_helper

    # Stripping the helper call from the real committed blob fails the guard.
    add_source = _function_source(_committed_source(), "add_reaction")
    assert add_source
    stripped = add_source.replace("_require_channel_view(", "_legacy_view_gate(")
    assert _classify(stripped) == (False, False)
