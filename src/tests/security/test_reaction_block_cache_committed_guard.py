"""Committed-tree guard: block/unblock reaction-cache invalidation cannot be
staged away.

A block/unblock changes every viewer's two-way-block-filtered reaction pills
(count + ``me``), which are memoized in the HTTP ``reaction_read_api`` body cache
and the cached message-list bodies. Without an eager drop on the relationship
write, a viewer who blocks a reactor keeps seeing that reactor's pill/count from
a warm body until the short TTL lapses. The fix adds a fail-closed hook to both
``block_user`` and ``unblock_user`` in the relationship manager.

Like the canonical committed-tree guards, every read here goes through
``git show HEAD:<path>`` -- never the working tree or the index -- so a stale
staged copy cannot mask a partial revert.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/relationships/manager/blocking.py"

# Distinctive markers that exist together only in the repaired committed blob.
_MARKERS = {
    "hook-defined": "def _invalidate_reaction_view_caches",
    "hook-called-twice": "_invalidate_reaction_view_caches()",
    "http-invalidation-import": (
        "from src.api.routes.reactions import _invalidate_reaction_read_cache"
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


def _evaluate(name: str, reader) -> bool:
    blob = reader(_TARGET)
    if name == "hook-called-twice":
        # Must be invoked from BOTH block_user and unblock_user.
        return blob.count(_MARKERS[name]) >= 2
    return _MARKERS[name] in blob


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


@pytest.mark.parametrize("name", sorted(_MARKERS))
def test_reaction_block_cache_fix_marker_present_in_committed_tree(
    name: str, commit_sha: str
) -> None:
    """Each distinctive fix marker is live in the committed blocking.py blob."""
    assert _evaluate(name, _head_blob_reader(commit_sha)), (
        f"COMMITTED REACTION-BLOCK CACHE FIX MISSING ({name}): "
        f"{_MARKERS[name]!r} not found in {commit_sha}:{_TARGET}"
    )


def test_reaction_block_cache_fix_marker_registry_is_non_empty() -> None:
    assert _MARKERS, "marker registry is empty - guard would pass vacuously"
    assert len(set(_MARKERS.values())) == len(_MARKERS), "duplicate fix markers"


@pytest.mark.parametrize("name", sorted(_MARKERS))
def test_synthetic_reverted_blob_is_flagged(name: str) -> None:
    """Non-vacuity: the exact production evaluator rejects a stale blob."""
    stale = "# stale staged copy\n# fix marker erased\n"

    def stale_blob(path: str) -> str:
        assert path == _TARGET
        return stale

    assert not _evaluate(name, stale_blob), (
        f"synthetic stale blob for {name!r} passed the committed-tree guard"
    )
