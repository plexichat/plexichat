"""Committed-tree pin: profile visibility fails closed on unwired settings.

``_get_profile_visibility`` used to treat an unwireable settings module as "no
stored visibility" and fall back to the public default, so a profile whose row
held ``private`` / ``friends`` / ``servers`` was served in full to any viewer
whenever that module was unwired. The fix resolves to the restricted
``limited`` projection in that state.

This workspace has peers staging stale whole-file copies, so pin the invariant
against the COMMITTED blob (``git show HEAD:...``) rather than the working tree:
a future staged revert cannot silently reintroduce the fail-open. This is a
standalone guard on purpose -- the shared security-marker registry is contested
by other agents, and a self-contained pin cannot be clobbered by their edits.

Non-vacuity: a synthetic vulnerable function is not classified as fail-closed,
a synthetic fixed one is, and the reader proves the real committed blob really
contained the ``_get_profile_visibility`` function.
"""

from __future__ import annotations

import ast
import subprocess
from functools import lru_cache
from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit, pytest.mark.security]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_REL = "src/api/routes/features/profiles.py"


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


def _is_unwired_settings_test(test) -> bool:
    """``if not api.get_settings()`` / ``if api.get_settings() is None``."""
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return _name(test.operand) == "get_settings"
    if isinstance(test, ast.Compare):
        if _name(test.left) != "get_settings":
            return False
        return any(
            isinstance(op, (ast.Is, ast.Eq))
            and isinstance(comp, ast.Constant)
            and comp.value is None
            for op, comp in zip(test.ops, test.comparators)
        )
    return False


def _classify(func_source: str) -> tuple[bool, bool]:
    """Return ``(has_unwired_deny, has_unwired_public_default)``."""
    tree = ast.parse(func_source)
    deny = False
    public = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _is_unwired_settings_test(node.test):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Name):
                if sub.value.id == "_FAIL_CLOSED_VISIBILITY":
                    deny = True
                if sub.value.id == "_DEFAULT_VISIBILITY":
                    public = True
    return deny, public


def test_committed_visibility_gate_is_fail_closed():
    src = _function_source(_committed_source(), "_get_profile_visibility")
    assert src, "guard went blind: _get_profile_visibility missing from committed blob"
    deny, public = _classify(src)
    assert deny, (
        "committed _get_profile_visibility has no unwired-settings deny; a stored "
        "private profile would be served as public when settings is unwired"
    )
    assert not public, (
        "committed _get_profile_visibility falls back to the public default on "
        "an unwired settings module (fail-open)"
    )


def test_guard_is_not_vacuous():
    vulnerable = """\
def _get_profile_visibility(owner_id):
    raw = _read_profile_visibility_raw(int(owner_id))
    if not raw:
        return _DEFAULT_VISIBILITY
    return _VISIBILITY_ALIASES.get(raw, _FAIL_CLOSED_VISIBILITY)
"""
    fixed = """\
def _get_profile_visibility(owner_id):
    if not api.get_settings():
        return _FAIL_CLOSED_VISIBILITY
    raw = _read_profile_visibility_raw(int(owner_id))
    if not raw:
        return _DEFAULT_VISIBILITY
    return _VISIBILITY_ALIASES.get(raw, _FAIL_CLOSED_VISIBILITY)
"""
    assert _classify(vulnerable) == (False, False)
    assert _classify(fixed) == (True, False)

    src = _function_source(_committed_source(), "_get_profile_visibility")
    assert src
    # The committed function really carries the gate, not a vacuous match.
    assert "get_settings" in src and "_FAIL_CLOSED_VISIBILITY" in src
