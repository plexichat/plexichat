"""Committed-tree guard: participant removal must retire the cached DM audience.

``EventRouter._dm_participant_cache`` memoizes the filtered DM/group participant
audience. A user removed from a conversation who remains in that memoized set
keeps receiving private events routed with the stale roster. The authoritative
fix lives in ``ParticipantService``: both the single removal and the bulk
removal paths call ``_invalidate_dm_participant_audience()``, which clears the
gateway's memoized audience.

This standalone guard reads ``participant.py`` straight out of ``HEAD`` (via
``git show``) -- never the working tree -- so a stale whole-file stage cannot
hide a committed revert. It is deliberately standalone because the shared
committed-tree marker registry is concurrently edited by other agents.

Three things are pinned structurally (AST, not just literals):

1. a module-level ``_invalidate_dm_participant_audience`` helper exists and
   calls ``clear_dm_participant_cache``;
2. ``remove_participant`` calls that helper;
3. ``remove_user_from_multiple_conversations`` calls that helper.

Non-vacuity: each pin has a syntactically valid synthetic revert that the same
evaluator rejects, plus a fully gutted blob.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/messaging/services/participant.py"

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed(commit: str) -> str:
    proc = _git("show", f"{commit}:{_TARGET}")
    assert proc.returncode == 0, (
        f"cannot read committed {_TARGET!r} from {commit}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _func(tree: ast.AST, name: str) -> ast.AST | None:
    return next(
        (
            node
            for node in tree.body
            if isinstance(node, FUNC_TYPES) and node.name == name
        ),
        None,
    )


def _calls_named(node: ast.AST, name: str) -> list[ast.Call]:
    out: list[ast.Call] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        called = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if called == name:
            out.append(sub)
    return out


def _evaluate(source: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"committed participant.py does not parse: {exc}"

    helper = _func(tree, "_invalidate_dm_participant_audience")
    if helper is None:
        return False, "module-level _invalidate_dm_participant_audience helper missing"
    if not _calls_named(helper, "clear_dm_participant_cache"):
        return False, "invalidation helper does not call clear_dm_participant_cache"

    for method in ("remove_participant", "remove_user_from_multiple_conversations"):
        # ParticipantService is a class: look for its methods directly.
        cls = next(
            (
                n
                for n in tree.body
                if isinstance(n, ast.ClassDef) and n.name == "ParticipantService"
            ),
            None,
        )
        if cls is None:
            return False, "ParticipantService class missing from committed blob"
        method_node = next(
            (n for n in cls.body if isinstance(n, FUNC_TYPES) and n.name == method),
            None,
        )
        if method_node is None:
            return False, f"{method} missing from committed blob"
        if not _calls_named(method_node, "_invalidate_dm_participant_audience"):
            return False, f"{method} does not invalidate the cached DM audience"

    return True, "both removal paths retire the cached DM participant audience"


def test_removal_cache_invalidation_pinned_at_head(commit_sha: str) -> None:
    ok, detail = _evaluate(_committed(commit_sha))
    assert ok, detail


_INVALIDATE_CALL = "        _invalidate_dm_participant_audience()"

_REVERTS: tuple[tuple[str, str, str, int], ...] = (
    (
        "first-removal-call-dropped",
        _INVALIDATE_CALL,
        "        pass  # reverted: no audience invalidation",
        0,
    ),
    (
        "second-removal-call-dropped",
        _INVALIDATE_CALL,
        "        pass  # reverted: no audience invalidation",
        -1,
    ),
)


@pytest.mark.parametrize("name,old,new,idx", _REVERTS, ids=[r[0] for r in _REVERTS])
def test_each_removal_pin_is_falsifiable(
    commit_sha: str, name: str, old: str, new: str, idx: int
) -> None:
    """A synthetic revert of either pinned call site is caught."""
    source = _committed(commit_sha)
    assert source.count(old) >= 1, f"revert fixture {name!r} no longer matches source"
    # Revert one occurrence at a time; the evaluator must reject each.
    parts = source.split(old)
    pos = idx if idx >= 0 else source.count(old) - 1
    reverted = old.join(parts[: pos + 1]) + new + old.join(parts[pos + 1 :])
    ok, detail = _evaluate(reverted)
    assert not ok, f"synthetic removal-cache revert {name!r} was not caught: {detail}"


def test_gutted_blob_is_rejected() -> None:
    ok, detail = _evaluate("# gutted participant service\n")
    assert not ok
    assert "helper" in detail or "ParticipantService" in detail
