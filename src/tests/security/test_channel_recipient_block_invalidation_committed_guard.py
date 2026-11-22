"""Committed-tree guard for the stale channel/DM fan-out eviction fix.

Block / unblock changes the block-filtered recipient audience for typing,
read-receipt, reaction, poll and DM message events. The gateway memoizes those
audiences in bounded, 1s-TTL caches that are keyed by scope + actor + source
identity (and RBAC epoch) but NOT by any block version:

* ``EventRouter._presence_audience_cache`` -> PRESENCE_UPDATE recipients;
* ``EventRouter._dm_participant_cache``     -> DM message event recipients;
* ``gateway_emit._channel_recipient_cache`` -> channel ephemeral-event recipients.

The shared mutation funnel ``RelationshipHelpersMixin._invalidate_all_relationships_cache``
evicted the presence audience (and DB-tier caches) but NOT the DM-participant or
channel-recipient audiences. A just-blocked user therefore kept receiving the
actor's ephemeral frames until the TTL lapsed. The fix made the funnel eager-evict
all three caches.

This guard reads ``src/core/relationships/manager/helpers.py`` straight out of
``HEAD`` with ``git show`` -- never the working tree -- so a stale staged copy
of that file cannot silently revert the fix before any behavioural test runs.
The evaluator is structural (AST): it fails unless all three ``clear_*`` names
are both imported by and *called* inside the funnel method, so a comment-only
mention or a dropped call is not enough.

Non-vacuity: syntactically valid synthetic reverts (dropping each of the two new
evict calls, and dropping an import) are rejected by the same evaluator, and a
fully gutted blob is rejected too.
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/relationships/manager/helpers.py"
_CLASS = "RelationshipHelpersMixin"
_METHOD = "_invalidate_all_relationships_cache"

FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)

# The three audience caches the funnel must eager-evict on a relationship write.
_REQUIRED = frozenset(
    {
        "clear_presence_audience_cache",
        "clear_dm_participant_cache",
        "clear_channel_recipient_cache",
    }
)


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
    """Resolve HEAD once per run; every blob read is pinned to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed(commit: str, path: str) -> str:
    proc = _git("show", f"{commit}:{path}")
    assert proc.returncode == 0, (
        f"cannot read committed {path!r} from {commit}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _evaluate(source: str) -> tuple[bool, str]:
    """Return ``(ok, detail)`` for the committed helpers module source."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"committed helpers module does not parse: {exc}"

    cls = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == _CLASS
        ),
        None,
    )
    if cls is None:
        return False, f"{_CLASS} class missing from committed blob"

    method = next(
        (
            node
            for node in cls.body
            if isinstance(node, FUNC_TYPES) and node.name == _METHOD
        ),
        None,
    )
    if method is None:
        return False, f"{_CLASS}.{_METHOD} missing from committed blob"

    segment = ast.get_source_segment(source, method)
    if not segment:
        return False, f"cannot recover source of {_CLASS}.{_METHOD}"

    try:
        method_tree = ast.parse(segment)
    except SyntaxError as exc:
        return False, f"funnel method source does not parse: {exc}"

    imported: set[str] = set()
    called: set[str] = set()
    for node in ast.walk(method_tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in _REQUIRED:
                called.add(name)

    missing_imports = _REQUIRED - imported
    if missing_imports:
        return False, (
            "funnel does not import the audience-eviction helpers: "
            + ", ".join(sorted(missing_imports))
        )
    missing_calls = _REQUIRED - called
    if missing_calls:
        return False, (
            "funnel does not eager-evict all block-filtered audience caches: "
            + ", ".join(sorted(missing_calls))
        )
    return True, "all three audience caches eager-evicted in committed funnel"


def test_stale_fanout_eviction_pinned_at_head(commit_sha: str) -> None:
    """The three-cache eviction is present in the committed funnel at HEAD."""
    ok, detail = _evaluate(_committed(commit_sha, _TARGET))
    assert ok, detail


# Each mutation is a syntactically valid revert of one part of the fix. The
# same evaluator used for the live HEAD check must reject every one, so a
# detector that never fires cannot pass.
_REVERTS: tuple[tuple[str, str, str], ...] = (
    (
        "no-dm-participant-evict",
        "        clear_dm_participant_cache()\n",
        "",
    ),
    (
        "no-channel-recipient-evict",
        "        clear_channel_recipient_cache()\n",
        "",
    ),
    (
        "no-presence-audience-evict",
        "        clear_presence_audience_cache()\n",
        "",
    ),
    (
        "no-dm-participant-import",
        "            clear_dm_participant_cache,\n",
        "",
    ),
    (
        "no-channel-recipient-import",
        "        from src.core.events.gateway_emit import clear_channel_recipient_cache\n",
        "",
    ),
)


@pytest.mark.parametrize("name,old,new", _REVERTS, ids=[r[0] for r in _REVERTS])
def test_each_pin_is_falsifiable(
    commit_sha: str, name: str, old: str, new: str
) -> None:
    """A synthetic revert of each eviction is caught by the evaluator."""
    source = _committed(commit_sha, _TARGET)
    assert old in source, f"revert fixture {name!r} no longer matches committed source"
    ok, detail = _evaluate(source.replace(old, new, 1))
    assert not ok, f"synthetic revert {name!r} was not caught: {detail}"


def test_gutted_blob_is_rejected() -> None:
    """A blob with no relationship mutation funnel at all cannot pass."""
    ok, detail = _evaluate("# gutted helpers module\n")
    assert not ok
    assert "RelationshipHelpersMixin" in detail or "_invalidate" in detail
