"""Committed-tree guard: whole-file replacement cannot rewrite locked cells.

99a77a3f (N812) fixed a PlexiSum ACL bypass. ``upload_file`` and
``restore_snapshot`` publish an arbitrary workbook SQLite blob straight to the
revision store instead of funnelling cell writes through ``apply_operation``'s
per-cell ``_check_range_permissions`` gate. An ``edit`` grantee could download
the workbook, rewrite a cell inside an owner-created ``read_only`` band, and
upload (or restore) the forgery back; the range ACL lives in the main database,
not in the blob, so no other gate noticed the overwrite. The fix adds
``_check_replacement_range_permissions`` -- which compares protected cells
between the current and replacement blobs and refuses changed, added or removed
ones for non-owners -- and invokes it from both whole-file write paths.

This guard reads the blobs out of the COMMITTED tree with ``git show`` -- never
the working tree -- so a stale staged whole-file copy that drops the helper or
either call site is caught even while the working copy still carries the fix.
Non-vacuity is proven three ways: a synthetic blob with each distinctive block
erased is flagged, the reader is shown to return the committed blob rather than
a divergent working copy, and the registry is checked for well-formedness and
for each marker's exact expected location.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_WORKBOOKS_PATH = "src/core/suite/sum/manager_workbooks.py"
_SNAPSHOTS_PATH = "src/core/suite/sum/manager_snapshots.py"
_PROTOCOL_PATH = "src/core/suite/sum/manager_protocol.py"

# Helper definition (signature + fail-closed docstring headline). Pinning the
# definition block, not just the name, means a stale copy that keeps the call
# sites but guts the body back to a no-op is caught too.
_CHECK_HELPER_DEF = (
    "    def _check_replacement_range_permissions(\n"
    "        self,\n"
    "        workbook: Dict[str, Any],\n"
    "        user_id: int,\n"
    "        current_bytes: bytes,\n"
    "        replacement_bytes: bytes,\n"
    "    ) -> None:\n"
    '        """Fail closed when a whole-file write would rewrite protected cells.'
)

# The owner type-confusion guard plus the authoritative read_only-band query.
# ``type(owner_id) is int`` deliberately rejects bool / int-like owners, and
# the bands come from the main DB using the already-fetched workbook row.
_OWNER_AND_BANDS = (
    '        owner_id = workbook["owner_id"]\n'
    "        if type(owner_id) is int and owner_id == int(user_id):\n"
    "            return\n"
    "        read_only = self._db.fetch_all(\n"
)

# The sheet-remap denial: a non-owner must not sidestep the DB-keyed bands by
# remapping/renaming sheets inside the replacement blob.
_SHEET_REMAP_DENIAL = (
    "            if current_sheets != replacement_sheets:\n"
    "                raise SumPermissionError(\n"
    '                    "Cannot change workbook sheets through a file replacement"\n'
    "                )"
)

# The terminal fail-closed check: any changed, added or removed protected cell
# is a write to that range and must be refused.
_PROTECTED_CELL_DENIAL = (
    "        # A changed, added, or removed protected cell is a write to that range.\n"
    "        if before != after:\n"
    '            raise SumPermissionError("Cannot change cells inside a protected range")'
)

# N902: the comparison must build before/after maps from the MERGED view the
# viewport actually renders -- sum_cells rows PLUS decoded sum_column_segments
# gap-fillers. Without this, a forged blob can insert a segment at an EMPTY
# protected coordinate while leaving sum_cells untouched, so before == after
# and the injected value renders inside a read_only band after promotion.
_SEGMENT_INCLUSIVE_COMPARISON = (
    "                # The viewport renders a merged view: sum_cells rows plus\n"
    "                # sum_column_segments cells that fill gaps where no sparse row\n"
    "                # exists. Compare that same merged view, not just sum_cells;\n"
)

# Shared call text used by both whole-file write paths. Pinning it per-path
# catches a partial revert that rewires only one of upload / restore.
_CALL_SITE = (
    "        self._check_replacement_range_permissions(\n"
    "            workbook, user_id, self._file_bytes(int(workbook_id)), db_bytes\n"
    "        )"
)

# The protocol declaration keeps the helper a first-class manager method.
_PROTOCOL_STUB = (
    "    def _check_replacement_range_permissions(self, *args: Any, **kwargs: Any) -> Any:\n"
    "        raise NotImplementedError"
)


@dataclass(frozen=True)
class ReplacementRangeMarker:
    """One committed-tree pin for the N812 whole-file-replacement ACL fix."""

    key: str
    path: str
    marker: str
    why: str


COMMITTED_REPLACEMENT_RANGE_MARKERS: tuple[ReplacementRangeMarker, ...] = (
    ReplacementRangeMarker(
        key="99a77a3f-replacement-range-helper-definition",
        path=_WORKBOOKS_PATH,
        marker=_CHECK_HELPER_DEF,
        why=(
            "99a77a3f: the helper that compares protected cells between the "
            "current and replacement blobs. Without it a non-owner's forged "
            "whole-file upload or snapshot restore silently rewrites read_only "
            "cells."
        ),
    ),
    ReplacementRangeMarker(
        key="99a77a3f-replacement-range-owner-type-and-bands",
        path=_WORKBOOKS_PATH,
        marker=_OWNER_AND_BANDS,
        why=(
            "99a77a3f: the owner check uses type() is int (rejecting bool / "
            "int-like confusion) and the read_only bands come from the "
            "authoritative main DB keyed by the already-fetched workbook row."
        ),
    ),
    ReplacementRangeMarker(
        key="99a77a3f-replacement-range-sheet-remap-denial",
        path=_WORKBOOKS_PATH,
        marker=_SHEET_REMAP_DENIAL,
        why=(
            "99a77a3f: range bands are keyed by sheet_id in the main DB, so a "
            "non-owner must not sidestep them by remapping sheets in the "
            "replacement blob."
        ),
    ),
    ReplacementRangeMarker(
        key="99a77a3f-replacement-range-protected-cell-denial",
        path=_WORKBOOKS_PATH,
        marker=_PROTECTED_CELL_DENIAL,
        why=(
            "99a77a3f: the terminal fail-closed comparison -- changed, added "
            "or removed protected cells are a write to that range and are "
            "refused for non-owners."
        ),
    ),
    ReplacementRangeMarker(
        key="n902-segment-inclusive-protected-cell-comparison",
        path=_WORKBOOKS_PATH,
        marker=_SEGMENT_INCLUSIVE_COMPARISON,
        why=(
            "N902: protected-cell maps must be built from the merged view the "
            "viewport renders (sum_cells rows plus decoded sum_column_segments "
            "gap-fillers). Without this a forged blob can insert a segment at "
            "an empty protected coordinate without tripping before != after, "
            "and the injected value renders inside a read_only band."
        ),
    ),
    ReplacementRangeMarker(
        key="99a77a3f-replacement-range-upload-call",
        path=_WORKBOOKS_PATH,
        marker=_CALL_SITE,
        why=(
            "99a77a3f: upload_file must invoke the check before promoting the "
            "blob; a stale copy that drops this call reopens the forged-upload "
            "bypass."
        ),
    ),
    ReplacementRangeMarker(
        key="99a77a3f-replacement-range-restore-call",
        path=_SNAPSHOTS_PATH,
        marker=_CALL_SITE,
        why=(
            "99a77a3f: restore_snapshot is the sibling whole-file write path; "
            "pinning it independently catches a partial revert that fixes only "
            "upload_file."
        ),
    ),
    ReplacementRangeMarker(
        key="99a77a3f-replacement-range-protocol-stub",
        path=_PROTOCOL_PATH,
        marker=_PROTOCOL_STUB,
        why=(
            "99a77a3f: the method is declared on SumManagerProtocol so mixin "
            "resolution keeps it a first-class manager operation."
        ),
    ),
)

PIN_KEYS: frozenset[str] = frozenset(m.key for m in COMMITTED_REPLACEMENT_RANGE_MARKERS)


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


def _evaluate(record: ReplacementRangeMarker, reader) -> tuple[bool, str]:
    try:
        text = reader(record.path)
    except (BlobMissing, OSError, UnicodeDecodeError) as exc:
        return False, (
            f"COMMITTED REPLACEMENT-RANGE ACL MARKER MISSING: {record.key!r} -- "
            f"{record.path!r} unreadable in commit ({exc}). Purpose: {record.why}"
        )
    if record.marker in text:
        return True, f"marker for {record.key!r} present in committed {record.path!r}"
    return False, (
        f"COMMITTED REPLACEMENT-RANGE ACL MARKER MISSING: {record.key!r} -- "
        f"distinctive block {record.marker} not found in committed "
        f"{record.path!r}. Purpose: {record.why}"
    )


@pytest.mark.parametrize(
    "record",
    COMMITTED_REPLACEMENT_RANGE_MARKERS,
    ids=[m.key for m in COMMITTED_REPLACEMENT_RANGE_MARKERS],
)
def test_replacement_range_marker_present_at_head(
    record: ReplacementRangeMarker, commit_sha: str
) -> None:
    """Every pinned N812 whole-file-replacement ACL site is present in HEAD."""
    present, detail = _evaluate(record, _make_blob_reader(commit_sha))
    assert present, detail


def test_registry_is_well_formed() -> None:
    """The registry is non-empty, unique, documented and points at known files."""
    keys = [m.key for m in COMMITTED_REPLACEMENT_RANGE_MARKERS]
    assert keys and len(keys) == len(set(keys))
    assert PIN_KEYS == set(keys)
    assert len(PIN_KEYS) == 8
    assert {_WORKBOOKS_PATH, _SNAPSHOTS_PATH, _PROTOCOL_PATH} == {
        m.path for m in COMMITTED_REPLACEMENT_RANGE_MARKERS
    }
    for record in COMMITTED_REPLACEMENT_RANGE_MARKERS:
        assert record.marker and record.path and record.why


@pytest.mark.parametrize(
    "record",
    COMMITTED_REPLACEMENT_RANGE_MARKERS,
    ids=[m.key for m in COMMITTED_REPLACEMENT_RANGE_MARKERS],
)
def test_synthetic_reverted_blob_is_flagged(record: ReplacementRangeMarker) -> None:
    """Non-vacuity: a stale blob with the whole-file check erased is flagged."""

    def reverted_blob(path: str) -> str:
        return (
            f"# stale plexisum manager for {path}\n"
            "# whole-file writes publish the blob with no range-ACL check\n"
            "        return result\n"
        )

    present, detail = _evaluate(record, reverted_blob)
    assert not present, f"synthetic revert of {record.key!r} was not caught"
    assert record.key in detail and record.marker in detail
    assert "COMMITTED REPLACEMENT-RANGE ACL MARKER MISSING" in detail


def test_reader_returns_committed_blob_not_stale_working_tree(tmp_path: Path) -> None:
    """Non-vacuity: the reader really reads HEAD, not a divergent working copy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "manager_workbooks.py"
    target.write_text(_CHECK_HELPER_DEF + "\n", encoding="utf-8")
    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "manager_workbooks.py", cwd=repo).returncode == 0
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
        "# whole-file writes publish the blob with no range-ACL check\n",
        encoding="utf-8",
    )
    assert _CHECK_HELPER_DEF not in target.read_text(encoding="utf-8")

    record = ReplacementRangeMarker(
        key="tmp-committed-vs-working",
        path="manager_workbooks.py",
        marker=_CHECK_HELPER_DEF,
        why="self-test only",
    )
    present, detail = _evaluate(record, _make_blob_reader(sha, repo_root=repo))
    assert present, detail
