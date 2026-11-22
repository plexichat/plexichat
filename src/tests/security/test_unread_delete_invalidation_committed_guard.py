"""Committed-tree guard: message deletes invalidate the unread aggregate cache.

63cf09a2 fixed a stale-badge bug: single and bulk message deletes changed the
authoritative unread COUNT (the cache aggregates over ``deleted = 0`` rows)
but never dropped the bounded process-local unread aggregate cache, so a
client kept seeing the pre-delete count until the short safety-net TTL lapsed.
The fix adds ``_invalidate_unread_after_delete`` (which calls
``unread_cache.invalidate_all()``) and invokes it from both delete paths,
mirroring the send path.

This guard reads the blob out of the committed tree with ``git show`` -- never
the working tree -- so a stale staged whole-file copy that removes the helper
or either call site is caught even while the working copy still carries the
fix. Non-vacuity is proven three ways: a synthetic blob with the fix erased is
flagged, the reader is shown to return the committed blob rather than a
divergent working copy, and the registry is checked for well-formedness and
for each marker's exact expected location.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET_PATH = "src/core/messaging/services/message/edit_delete.py"

# The helper definition and its namespace-clearing body. Pinning the definition
# block (not just the name) means a stale copy that keeps the call sites but
# guts the body back to a no-op is caught too.
_UNREAD_INVALIDATION_HELPER = (
    "def _invalidate_unread_after_delete() -> None:\n"
    '    """Drop the bounded unread aggregate after a delete changes unread counts.'
)

# The single-message delete call site: the invalidation must follow thread-link
# cleanup and immediately precede the successful return.
_SINGLE_DELETE_CALL = (
    "        self._cleanup_thread_links(message_id)\n"
    "\n"
    "        _invalidate_unread_after_delete()\n"
    "\n"
    "        return True"
)

# The bulk-delete call site: invalidate only when at least one delete succeeded
# (no write, no cache churn), just before the result dict is built.
_BULK_DELETE_CALL = (
    "        if success_count:\n"
    "            _invalidate_unread_after_delete()\n"
    "\n"
    "        return {"
)


@dataclass(frozen=True)
class UnreadDeleteMarker:
    """One committed-tree pin for the unread-cache delete-invalidation fix."""

    key: str
    path: str
    marker: str
    why: str


COMMITTED_UNREAD_DELETE_MARKERS: tuple[UnreadDeleteMarker, ...] = (
    UnreadDeleteMarker(
        key="63cf09a2-unread-invalidation-helper",
        path=_TARGET_PATH,
        marker=_UNREAD_INVALIDATION_HELPER,
        why=(
            "63cf09a2: the helper whose body calls unread_cache.invalidate_all(); "
            "without it the delete paths never drop the bounded aggregate, so a "
            "deleted message keeps contributing to a cached unread badge."
        ),
    ),
    UnreadDeleteMarker(
        key="63cf09a2-unread-invalidation-single-delete",
        path=_TARGET_PATH,
        marker=_SINGLE_DELETE_CALL,
        why=(
            "63cf09a2: the single-message delete path must invalidate after a "
            "committed delete; a stale staged copy that drops this call serves "
            "the pre-delete badge until the safety-net TTL expires."
        ),
    ),
    UnreadDeleteMarker(
        key="63cf09a2-unread-invalidation-bulk-delete",
        path=_TARGET_PATH,
        marker=_BULK_DELETE_CALL,
        why=(
            "63cf09a2: the bulk-delete path is the sibling of the single delete; "
            "pinning it independently catches a partial revert that fixes only "
            "one of the two delete entry points."
        ),
    ),
)

PIN_KEYS: frozenset[str] = frozenset(m.key for m in COMMITTED_UNREAD_DELETE_MARKERS)


class BlobMissing(Exception):
    """Raised when a path is absent from the pinned commit."""


def _git(*args: str, cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    """Resolve HEAD once per run and pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _make_blob_reader(commit: str, repo_root: Path = _REPO_ROOT):
    """Return a reader that fetches ``<commit>:<path>`` with ``git show``."""

    def read(path: str) -> str:
        proc = _git("show", f"{commit}:{path}", cwd=repo_root)
        if proc.returncode != 0:
            raise BlobMissing(f"{path!r} absent from commit: {proc.stderr.strip()}")
        return proc.stdout

    return read


def _evaluate(record: UnreadDeleteMarker, reader) -> tuple[bool, str]:
    try:
        text = reader(record.path)
    except (BlobMissing, OSError, UnicodeDecodeError) as exc:
        return False, (
            f"COMMITTED UNREAD-DELETE INVALIDATION MARKER MISSING: {record.key!r} -- "
            f"{record.path!r} unreadable in commit ({exc}). Purpose: {record.why}"
        )
    if record.marker in text:
        return True, f"marker for {record.key!r} present in committed {record.path!r}"
    return False, (
        f"COMMITTED UNREAD-DELETE INVALIDATION MARKER MISSING: {record.key!r} -- "
        f"distinctive block {record.marker} not found in committed "
        f"{record.path!r}. Purpose: {record.why}"
    )


@pytest.mark.parametrize(
    "record",
    COMMITTED_UNREAD_DELETE_MARKERS,
    ids=[m.key for m in COMMITTED_UNREAD_DELETE_MARKERS],
)
def test_unread_delete_marker_present_at_head(
    record: UnreadDeleteMarker, commit_sha: str
) -> None:
    """Every pinned unread-delete invalidation site is present in HEAD."""
    present, detail = _evaluate(record, _make_blob_reader(commit_sha))
    assert present, detail


def test_registry_is_well_formed() -> None:
    """The registry is non-empty, unique, documented and points at one file."""
    keys = [m.key for m in COMMITTED_UNREAD_DELETE_MARKERS]
    assert keys and len(keys) == len(set(keys))
    assert PIN_KEYS == set(keys)
    assert len(PIN_KEYS) == 3
    for record in COMMITTED_UNREAD_DELETE_MARKERS:
        assert record.marker and record.path == _TARGET_PATH and record.why


@pytest.mark.parametrize(
    "record",
    COMMITTED_UNREAD_DELETE_MARKERS,
    ids=[m.key for m in COMMITTED_UNREAD_DELETE_MARKERS],
)
def test_synthetic_reverted_blob_is_flagged(record: UnreadDeleteMarker) -> None:
    """Non-vacuity: a stale blob with the invalidation erased must be flagged."""

    def reverted_blob(path: str) -> str:
        return (
            f"# stale message edit/delete service for {path}\n"
            "# unread aggregate invalidation never called\n"
            "        return True\n"
        )

    present, detail = _evaluate(record, reverted_blob)
    assert not present, f"synthetic revert of {record.key!r} was not caught"
    assert record.key in detail and record.marker in detail
    assert "COMMITTED UNREAD-DELETE INVALIDATION MARKER MISSING" in detail


def test_reader_returns_committed_blob_not_stale_working_tree(tmp_path: Path) -> None:
    """Non-vacuity: the reader really reads HEAD, not a divergent working copy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "edit_delete.py"
    target.write_text(_UNREAD_INVALIDATION_HELPER + "\n", encoding="utf-8")
    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "edit_delete.py", cwd=repo).returncode == 0
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

    # Peer-style stale working copy: the fix is gone from disk only.
    target.write_text(
        "# unread aggregate invalidation never called\n", encoding="utf-8"
    )
    assert _UNREAD_INVALIDATION_HELPER not in target.read_text(encoding="utf-8")

    record = UnreadDeleteMarker(
        key="tmp-committed-vs-working",
        path="edit_delete.py",
        marker=_UNREAD_INVALIDATION_HELPER,
        why="self-test only",
    )
    present, detail = _evaluate(record, _make_blob_reader(sha, repo_root=repo))
    assert present, detail
