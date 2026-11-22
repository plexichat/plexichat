"""Committed-tree guard: the message-edit CAS dedup cannot be staged away.

The replay/concurrency fix landed in two files:

  * ``src/core/messaging/services/message/edit_delete.py`` -- ``edit_message``
    performs a revision compare-and-swap instead of a blind read-then-write,
    records edit history only for the winning write, and fails closed with a
    ``MessageConflictError`` for the stale loser (no second success that a
    route would turn into a second MESSAGE_UPDATE broadcast).
  * ``src/core/messaging/repositories/message.py`` -- the repository exposes
    ``update_content_if_revision`` whose single UPDATE carries the revision
    predicate, so two racing applications of the same edit cannot both win.

The behavioural test (``test_message_edit_replay_idempotency.py``) caught this
at runtime, but the test originally landed several commits BEFORE the fix:
for that window the committed tree had a failing test and an unfixed edit path.
This guard pins the fix to the committed tree so a stale staged copy of either
contested file cannot silently revert it and leave the tree inconsistent.

Every read goes through ``git show HEAD:<path>`` -- never the working tree or
the index -- so a stale staged copy cannot mask the check. This is a standalone
companion so it stays independent of the contested shared marker registry.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]

_SERVICE = "src/core/messaging/services/message/edit_delete.py"
_REPO = "src/core/messaging/repositories/message.py"

# Distinctive blocks that exist only in the repaired blobs. Each is checked
# independently so a partial revert is named precisely.
_SERVICE_CAS_CALL = (
    '        _cas = getattr(self._repo, "update_content_if_revision", None)\n'
    "        if callable(_cas):\n"
)
_SERVICE_FAIL_CLOSED = (
    "            if not applied:\n"
    "                raise MessageConflictError(\n"
    '                    "Message was modified by another request; refresh and retry",'
)
_REPO_CAS_METHOD = "    def update_content_if_revision(\n"
_REPO_CAS_PREDICATE = (
    "                 AND COALESCE(NULLIF(edited_at, 0), updated_at) = ?\n"
    "                 AND COALESCE(deleted, 0) = 0"
)

_MARKERS = {
    "service-cas-call": (_SERVICE, _SERVICE_CAS_CALL),
    "service-fail-closed": (_SERVICE, _SERVICE_FAIL_CLOSED),
    "repo-cas-method": (_REPO, _REPO_CAS_METHOD),
    "repo-cas-predicate": (_REPO, _REPO_CAS_PREDICATE),
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
    path, marker = _MARKERS[name]
    return marker in reader(path)


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


@pytest.mark.parametrize("name", sorted(_MARKERS))
def test_message_edit_cas_marker_present_in_committed_tree(
    name: str, commit_sha: str
) -> None:
    """Each distinctive CAS block is live in the committed blob."""
    assert _evaluate(name, _head_blob_reader(commit_sha)), (
        f"COMMITTED MESSAGE-EDIT CAS FIX MISSING ({name}): "
        f"{_MARKERS[name][1]!r} not found in {commit_sha}:{_MARKERS[name][0]}; "
        "a stale staged copy may have reverted the replay dedup fix"
    )


def test_message_edit_cas_marker_registry_is_non_empty() -> None:
    assert _MARKERS, "marker registry is empty - guard would pass vacuously"
    distinct = [marker for _path, marker in _MARKERS.values()]
    assert len(set(distinct)) == len(distinct), "duplicate fix markers"


@pytest.mark.parametrize("name", sorted(_MARKERS))
def test_synthetic_reverted_blob_is_flagged(name: str) -> None:
    """Non-vacuity: the exact production evaluator rejects a stale blob."""
    stale = "# stale staged copy of the edit path\n# CAS dedup erased\n"

    def stale_blob(path: str) -> str:
        assert path in (_SERVICE, _REPO)
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
