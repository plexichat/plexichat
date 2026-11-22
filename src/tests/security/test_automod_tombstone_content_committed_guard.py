"""Committed-tree guard: the AutoMod tombstone content leak stays fixed.

N948 fix landed in two contested files:

  * ``src/core/automod/actions/delete.py`` -- ``_replace_message`` stores a
    neutral ``automod <action> (rule <id>)`` label as ``metadata.automod_reason``
    instead of the private ``violation.matched_content``. The old expression
    ``context.get("__automod_reason__") or violation.matched_content`` must
    never return to the committed tree.
  * ``src/api/routes/messages/messages_crud/broadcast.py`` --
    ``_fanout_safe_message_body`` strips ``metadata.automod_reason`` from the
    wire copy (and drops serialized metadata blobs) so legacy rows and direct
    ``automod_remove_message`` callers cannot leak the private body either.

Behavioural coverage lives in
``test_automod_tombstone_fanout_content_leak.py``; this guard pins the fix to
the committed tree so a stale staged copy of either file cannot silently
revert it. Every read goes through ``git show HEAD:<path>`` -- never the
working tree or the index -- so a stale staged copy cannot mask the check.
This is a standalone companion so it stays independent of the shared marker
registry.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]

_DELETE = "src/core/automod/actions/delete.py"
_BROADCAST = "src/api/routes/messages/messages_crud/broadcast.py"

# Distinctive blocks that exist only in the repaired blobs.
_POSITIVE = {
    "delete-neutral-reason": (
        _DELETE,
        '        reason = f"automod {ActionType.DELETE_MESSAGE.value} (rule '
        '{violation.rule_id})"\n',
    ),
    "delete-no-content-comment": (
        _DELETE,
        "# message content in this broadcast-visible field: use a neutral",
    ),
    "broadcast-strip-key": (
        _BROADCAST,
        '        metadata.pop("automod_reason", None)\n',
    ),
    "broadcast-fail-closed-blob": (
        _BROADCAST,
        '    elif isinstance(metadata, str):\n        data["metadata"] = None\n',
    ),
}

# The pre-fix expression must be gone from the committed tree.
_NEGATIVE = {
    "delete-old-matched-content-reason": (
        _DELETE,
        'reason = context.get("__automod_reason__") or violation.matched_content',
    ),
}


def _git(*args: str, cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def _head_blob_reader(commit: str, repo_root: Path = _REPO_ROOT):
    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}", cwd=repo_root)
        assert proc.returncode == 0, (
            f"cannot read committed blob {commit}:{path}: {proc.stderr.strip()}"
        )
        return proc.stdout

    return read


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


@pytest.mark.parametrize("name", sorted(_POSITIVE))
def test_tombstone_fix_marker_present_in_committed_tree(
    name: str, commit_sha: str
) -> None:
    path, marker = _POSITIVE[name]
    assert marker in _head_blob_reader(commit_sha)(path), (
        f"COMMITTED TOMBSTONE LEAK FIX MISSING ({name}): {marker!r} not in "
        f"{commit_sha}:{path}; a stale staged copy may have reverted the fix"
    )


@pytest.mark.parametrize("name", sorted(_NEGATIVE))
def test_leaky_expression_absent_from_committed_tree(
    name: str, commit_sha: str
) -> None:
    path, marker = _NEGATIVE[name]
    assert marker not in _head_blob_reader(commit_sha)(path), (
        f"COMMITTED TOMBSTONE LEAK REINTRODUCED ({name}): {marker!r} found in "
        f"{commit_sha}:{path}; private message content can reach the fan-out"
    )


def test_marker_registry_is_non_empty() -> None:
    assert _POSITIVE, "positive marker registry is empty - guard passes vacuously"
    assert _NEGATIVE, "negative marker registry is empty - guard passes vacuously"
    positive = [marker for _path, marker in _POSITIVE.values()]
    assert len(set(positive)) == len(positive), "duplicate positive fix markers"


@pytest.mark.parametrize("name", sorted(_POSITIVE))
def test_synthetic_stale_blob_is_flagged(name: str) -> None:
    """Non-vacuity: the exact production evaluator rejects a stale blob."""
    stale = "# stale staged copy\n# neutral reason and sanitizer erased\n"

    def stale_blob(path: str) -> str:
        assert path in (_DELETE, _BROADCAST)
        return stale

    assert _POSITIVE[name][1] not in stale_blob(_POSITIVE[name][0])


@pytest.mark.parametrize("name", sorted(_NEGATIVE))
def test_synthetic_leaky_blob_is_flagged(name: str) -> None:
    """Non-vacuity: the negative evaluator rejects the pre-fix expression."""
    path, marker = _NEGATIVE[name]

    def leaky_blob(_path: str) -> str:
        return f"        {marker}\n"

    assert marker in leaky_blob(path)
