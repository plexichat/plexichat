"""Guard: critical security fixes must remain in the COMMITTED tree.

Background
----------
Several contested files were repaired through private-index commits (N448
``d39659c3``, N478 ``d6a4143``, N459 ``c6a111dd``).  The fix therefore exists
at ``HEAD`` while peer agents' working-tree/index copies of those same files
are *stale* -- they still contain the pre-fix code (``max(0, limit)``, a default
cache key, a curated file list instead of the structural sweep, ...).

If a peer later stages one of those whole stale files, the resulting commit can
silently REVERT the fix.  The existing regression tests would notice that at
test time, but a static/committed-tree guard catches it earlier and names the
exact fix that disappeared.

Design
------
This module is the *committed-tree* counterpart of the working-tree
``test_security_fix_marker_meta_guard.py``.  A single documented registry
(:data:`COMMITTED_FIX_MARKERS`) lists only the highest-risk markers from those
private-index repairs.  For each entry we read the blob straight out of the
commit via ``git show <sha>:<path>`` -- never the working tree -- so a stale
working copy cannot mask a committed revert (and cannot produce a false alarm
either).

HEAD is resolved once per test session and pinned for every blob read, which
keeps the check internally consistent even while other agents keep moving HEAD
concurrently; every run still reads the *current* HEAD at runtime.

Removal policy: a path that disappears from the commit is a failure, not a
silent pass.  Removal is tolerated only for an explicitly documented successor
(``successors``), mirroring the working-tree meta-guard's ``moved_to``.

Non-vacuity is proven twice: (1) a fake blob with the marker stripped out is
flagged for every registry entry, and (2) a throwaway git repo whose working
tree diverges from HEAD shows the reader really returns the committed blob.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


class BlobMissing(Exception):
    """Raised by a blob reader when a path is absent from the commit."""

    def __init__(self, path: str, reason: str = "") -> None:
        super().__init__(f"{path!r} not present in commit: {reason}")
        self.path = path
        self.reason = reason


@dataclass(frozen=True)
class CommittedFixMarker:
    """One critical fix that must be present in a committed blob.

    ``path`` is repo-relative.  ``successors`` are documented relocation
    targets: a primary-path removal is tolerated only when a successor blob
    carries the marker.  ``why`` records the security purpose and the stale-file
    revert that this entry is meant to catch.
    """

    key: str
    path: str
    marker: str
    why: str
    successors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def candidates(self) -> tuple[str, ...]:
        return (self.path,) + self.successors


COMMITTED_FIX_MARKERS: tuple[CommittedFixMarker, ...] = (
    CommittedFixMarker(
        key="n448-channel-detail-cache-key",
        path="src/api/routes/channels/crud.py",
        marker="key_builder=authenticated_cache_key(",
        why=(
            "N448: the cached channel-detail loader partitions its cache by "
            "credential; a stale staged copy drops key_builder and falls back "
            "to the default cache key"
        ),
    ),
    CommittedFixMarker(
        key="n448-server-channels-cache-key",
        path="src/api/routes/servers/channels.py",
        marker="key_builder=authenticated_cache_key(",
        why=(
            "N448 main: the server-channels listing cache is keyed by "
            "credential; stale copies silently restore the default cache key"
        ),
    ),
    CommittedFixMarker(
        key="n478-thread-messages-limit-clamp",
        path="src/core/threads/manager/messaging.py",
        marker="limit = _clamp_page_limit(limit)",
        why=(
            "N478: the thread message listing clamps LIMIT from above; a "
            "stale staged copy reverts to the bare max(0, limit) check and "
            "reopens unbounded-history reads"
        ),
    ),
    CommittedFixMarker(
        key="n478-mutual-suggestions-limit-clamp",
        path="src/core/relationships/manager/mutual.py",
        marker="limit = _clamp_page_limit(limit)",
        why=(
            "N478: the mutual/suggestions listing clamps LIMIT from above; a "
            "stale staged copy reverts to max(0, limit)"
        ),
    ),
    CommittedFixMarker(
        key="n459-server-route-structural-sweep",
        path="src/tests/security/test_server_route_id_parse_boundary.py",
        marker="def _iter_server_modules(",
        why=(
            "N459: the route-id boundary guard walks the whole server route "
            "tree structurally; a stale staged copy restores a curated file "
            "list and makes the sweep vacuous"
        ),
    ),
)


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
    """Resolve HEAD once per run, then pin every blob read to that commit."""
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
            raise BlobMissing(path, proc.stderr.strip())
        return proc.stdout

    return read


def _evaluate(record: CommittedFixMarker, reader) -> tuple[bool, str]:
    """Return ``(present, detail)`` using the injectable ``reader``.

    The reader is injectable so the self-tests can serve synthetic committed
    blobs (marker removed, file deleted, file relocated) without touching the
    real repository.
    """
    checked: list[str] = []
    for rel in record.candidates:
        try:
            text = reader(rel)
        except BlobMissing as exc:
            checked.append(f"{rel} (missing from commit: {exc.reason})")
            continue
        except (OSError, UnicodeDecodeError) as exc:
            checked.append(f"{rel} (unreadable: {exc.__class__.__name__})")
            continue
        if record.marker in text:
            return True, (
                f"marker {record.marker!r} for {record.key!r} present in "
                f"committed blob {rel!r}"
            )
        checked.append(rel)

    location = f"expected in committed blob {record.path!r}"
    if record.successors:
        location += f" or documented successor(s) {record.successors!r}"
    detail = (
        f"COMMITTED SECURITY FIX MARKER MISSING: {record.key!r} -- "
        f"{record.marker!r} not found ({location}); checked: "
        + ", ".join(checked)
        + f". Purpose: {record.why}"
    )
    return False, detail


@pytest.mark.parametrize(
    "record", COMMITTED_FIX_MARKERS, ids=[r.key for r in COMMITTED_FIX_MARKERS]
)
def test_committed_fix_marker_present(
    record: CommittedFixMarker, commit_sha: str
) -> None:
    """Every documented high-risk fix is present in the committed tree."""
    present, detail = _evaluate(record, _make_blob_reader(commit_sha))
    assert present, detail


def test_registry_is_documentable_and_unique() -> None:
    """The committed-tree registry is well-formed (no empty/duplicate rows)."""
    keys = [r.key for r in COMMITTED_FIX_MARKERS]
    assert keys, "registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate fix keys in registry"
    for record in COMMITTED_FIX_MARKERS:
        assert record.marker, f"empty marker for {record.key!r}"
        assert record.path.startswith("src/"), f"bad path for {record.key!r}"
        assert record.why, f"undocumented purpose for {record.key!r}"
        # These are the contested repairs: silently tolerating removal would
        # defeat the whole point of the guard, so no successors are allowed.
        assert not record.successors, f"undocumented removal allowed for {record.key!r}"


@pytest.mark.parametrize(
    "record", COMMITTED_FIX_MARKERS, ids=[r.key for r in COMMITTED_FIX_MARKERS]
)
def test_synthetic_reverted_blob_is_flagged(record: CommittedFixMarker) -> None:
    """Non-vacuity: a fake blob lacking the marker must fail the guard.

    This simulates a peer committing a stale staged file that reverts the fix:
    the evaluator has to notice it for *every* registry entry, not just pass
    vacuously on an unchecked path.
    """

    def reverted_blob(path: str) -> str:
        return f"# synthetic reverted blob for {path}\n# mark erased\n"

    present, detail = _evaluate(record, reverted_blob)
    assert not present, f"synthetic committed revert of {record.key!r} was not caught"
    assert record.key in detail and record.marker in detail
    assert "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_undocumented_blob_removal_is_flagged() -> None:
    """A file removed from the commit fails unless a successor is documented."""
    record = CommittedFixMarker(
        key="synthetic-removed-file",
        path="src/tests/security/_does_not_exist_committed_guard.py",
        marker="NEVER_PRESENT",
        why="self-test only",
    )

    def missing_blob(path: str) -> str:
        raise BlobMissing(path, "deleted in commit")

    present, detail = _evaluate(record, missing_blob)
    assert not present
    assert record.key in detail and "missing from commit" in detail


def test_documented_successor_is_tolerated() -> None:
    """A documented relocation is accepted; only documented removal is safe."""
    record = CommittedFixMarker(
        key="synthetic-relocated-file",
        path="old.py",
        marker="MARKER",
        why="self-test only",
        successors=("new.py",),
    )

    def relocated_blob(path: str) -> str:
        if path == "old.py":
            raise BlobMissing(path, "removed")
        return "# relocated fix\nMARKER\n"

    present, detail = _evaluate(record, relocated_blob)
    assert present, detail
    assert "new.py" in detail


def test_reader_returns_committed_blob_not_stale_working_tree(
    tmp_path: Path,
) -> None:
    """Non-vacuity: the reader really reads HEAD, not the working tree.

    A throwaway repo commits a file with the marker, then rewrites the working
    copy to a stale/reverted version.  The same reader used by the guard must
    still see the committed value, proving the check cannot be fooled by a
    stale working-tree/index file.
    """
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

    # Peer-style stale working copy: the fix is gone from disk/index only.
    target.write_text("MARKER_REMOVED\n", encoding="utf-8")
    assert "MARKER_PRESENT" not in target.read_text(encoding="utf-8")

    reader = _make_blob_reader(sha, repo_root=repo)
    record = CommittedFixMarker(
        key="tmp-committed-vs-working",
        path="fix.py",
        marker="MARKER_PRESENT",
        why="self-test only",
    )
    present, detail = _evaluate(record, reader)
    assert present, detail
