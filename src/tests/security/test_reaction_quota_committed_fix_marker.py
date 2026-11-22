"""Committed-tree guard: the atomic reaction-quota fix cannot be staged away.

The add-reaction TOCTOU fix (commit e159e7dc) added three things to
``src/core/reactions/manager/reactions.py``:

  * ``_ATOMIC_REACTION_INSERT_SQL`` -- one guarded INSERT that re-checks both
    quotas against live table state at insert time,
  * a per-message ``pg_advisory_xact_lock`` that serialises concurrent adds on
    PostgreSQL, and
  * a ``rowcount == 0`` fail-closed signal that maps a refused insert to the
    usual duplicate/quota errors.

Peer agents keep private-index/stale working-tree copies of this file: the
advisory-lock namespace is itself being edited (102 -> 103, to stop colliding
with the sticker quota), and other security fixes keep landing on top. Staging
a whole stale copy could silently drop any of the three pieces above and
re-open the race. The behavioural race test catches that at runtime, but a
*committed-tree* guard catches it at commit time and names what vanished.

Like the canonical ``test_security_fix_marker_committed_tree_guard.py``, every
read here goes through ``git show HEAD:<path>`` -- never the working tree or the
index -- so a stale staged copy cannot mask the check. This module is a
standalone companion so it stays independent of the contested shared registry.

Deliberate choice: this guard pins the lock's *presence*, not its namespace
number. The namespace is concurrently changing and is pinned by the structural
guard (``test_quota_advisory_lock_namespace_isolation.py``), which reads the
working tree. A committed-tree guard that named a specific namespace would go
red on the next namespace edit for no security gain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/reactions/manager/reactions.py"

# Distinctive markers that exist together only in the repaired committed blob.
# Each is checked independently so a partial revert is named precisely.
_MARKERS = {
    "atomic-guarded-insert": "_ATOMIC_REACTION_INSERT_SQL",
    "per-message-advisory-lock": "SELECT pg_advisory_xact_lock(?, ?)",
    "rowcount-fail-closed": 'getattr(cursor, "rowcount", 0) == 0',
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
    """Return a reader that fetches ``<commit>:<path>`` with ``git show``."""

    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}", cwd=repo_root)
        assert proc.returncode == 0, (
            f"cannot read committed blob {commit}:{path}: {proc.stderr.strip()}"
        )
        return proc.stdout

    return read


def _evaluate(name: str, reader) -> bool:
    """Return True iff the distinctive marker for ``name`` is in the blob."""
    return _MARKERS[name] in reader(_TARGET)


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


@pytest.mark.parametrize("name", sorted(_MARKERS))
def test_reaction_quota_fix_marker_present_in_committed_tree(
    name: str, commit_sha: str
) -> None:
    """Each distinctive fix marker is live in the committed reactions.py blob."""
    assert _evaluate(name, _head_blob_reader(commit_sha)), (
        f"COMMITTED REACTION QUOTA FIX MISSING ({name}): "
        f"{_MARKERS[name]!r} not found in {commit_sha}:{_TARGET}; a stale "
        "staged copy may have reverted the atomic add-reaction TOCTOU fix"
    )


def test_reaction_quota_fix_marker_registry_is_non_empty() -> None:
    assert _MARKERS, "marker registry is empty - guard would pass vacuously"
    assert len(set(_MARKERS.values())) == len(_MARKERS), "duplicate fix markers"


@pytest.mark.parametrize("name", sorted(_MARKERS))
def test_synthetic_reverted_blob_is_flagged(name: str) -> None:
    """Non-vacuity: the exact production evaluator rejects a stale blob."""
    stale = "# stale staged copy of reactions.py\n# fix marker erased\n"

    def stale_blob(path: str) -> str:
        assert path == _TARGET
        return stale

    assert not _evaluate(name, stale_blob), (
        f"synthetic stale blob for {name!r} passed the committed-tree guard"
    )


def test_reader_returns_committed_blob_not_stale_working_tree(
    tmp_path: Path,
) -> None:
    """Non-vacuity: the reader reads HEAD, not a stale disk/index copy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "fix.py"
    target.write_text("MARKER_PRESENT\n", encoding="utf-8")

    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "fix.py", cwd=repo).returncode == 0
    committed = _git(
        "-c",
        "user.email=guard@example.invalid",
        "-c",
        "user.name=guard",
        "commit",
        "-qm",
        "fix",
        cwd=repo,
    )
    assert committed.returncode == 0, committed.stderr
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    assert sha

    # Peer-style stale working copy with the fix gone from disk/index only.
    target.write_text("MARKER_REMOVED\n", encoding="utf-8")
    assert "MARKER_PRESENT" not in target.read_text(encoding="utf-8")

    reader = _head_blob_reader(sha, repo_root=repo)
    assert reader("fix.py").strip() == "MARKER_PRESENT"
