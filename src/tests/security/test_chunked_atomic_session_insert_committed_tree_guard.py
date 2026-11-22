"""Narrow committed-tree guard for the atomic chunked-session cap INSERT.

This is the committed-tree counterpart of
``test_chunked_session_cap_toctou.py`` (N684 / 7b9086a4). That behavioral test
proves the *effect* of the atomic guarded INSERT at runtime; this guard pins the
*code* in the committed blob, so a peer staging a whole stale copy of
``src/core/media/chunked.py`` cannot silently regress it before the behavioral
test ever runs.

The guarded INSERT is security-critical: a bare ``INSERT`` issued after a
separate ``SELECT COUNT(*)`` lets two concurrent callers both read a below-cap
count and both insert, pushing one user past ``max_active_sessions_per_user``
and letting them hoard sparse temp files (a per-user cap bypass of the same
class already fixed for emoji/sticker/role/app quotas). The fix is three
coupled pieces, all pinned here:

* ``_ATOMIC_SESSION_INSERT_SQL``: a single ``INSERT ... SELECT ... WHERE
  (SELECT COUNT(*)) < ?`` statement so check-and-insert are atomic.
* its use in ``create_session`` (not a separate bare INSERT),
* the ``rowcount != 1`` fail-closed branch that rolls back and unlinks the
  pre-allocated temp file, leaving no over-cap row and no orphaned file.

The reader uses ``git show <HEAD>:src/core/media/chunked.py`` -- never the
working tree -- so it sees exactly what a peer would commit. HEAD is resolved
once per run.

Non-vacuity: a synthetic stale blob with each guard erased is flagged by the
exact same evaluator used for the real committed blob.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CHUNKED_PATH = "src/core/media/chunked.py"
_PIN_KEY = "chunked-atomic-session-insert"

# Exact-whitespace pin of the full atomic guarded INSERT definition. The
# distinctive part is the INSERT ... SELECT with the ``WHERE (SELECT COUNT(*)
# ... ) < ?`` cap guard: a stale copy reverts to ``INSERT ... VALUES`` and drops
# it entirely.
ATOMIC_INSERT_SQL_MARKER = (
    '_ATOMIC_SESSION_INSERT_SQL = """\n'
    "INSERT INTO media_upload_sessions\n"
    "    (id, user_id, filename, content_type, total_size, chunk_size,\n"
    "     total_chunks, uploaded_chunks, uploaded_bytes, status,\n"
    "     created_at, updated_at, expires_at, temp_path)\n"
    "SELECT ?, ?, ?, ?, ?, ?, ?, 0, 0, 'pending', ?, ?, ?, ?\n"
    "WHERE (\n"
    "    SELECT COUNT(*) FROM media_upload_sessions\n"
    "    WHERE user_id = ? AND expires_at > ?\n"
    "      AND status NOT IN ('completed', 'expired')\n"
    ") < ?\n"
    '"""'
)

# create_session must execute the guarded statement, not a bare INSERT.
GUARDED_INSERT_USAGE_MARKER = "                _ATOMIC_SESSION_INSERT_SQL,\n"

# The rowcount fail-closed branch: rowcount == 0 (or anything but 1) returns
# None after rolling back and unlinking the pre-allocated temp file, so a
# rejected create leaves neither an over-cap row nor an orphaned file.
ROWCOUNT_UNLINK_FAIL_CLOSED_MARKER = (
    '            if getattr(cursor, "rowcount", 0) != 1:\n'
    "                if is_postgres:\n"
    "                    self._db.rollback()\n"
    "                try:\n"
    "                    os.unlink(temp_path)\n"
    "                except OSError:\n"
    "                    pass"
)

_ALL_MARKERS = (
    ATOMIC_INSERT_SQL_MARKER,
    GUARDED_INSERT_USAGE_MARKER,
    ROWCOUNT_UNLINK_FAIL_CLOSED_MARKER,
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
    """Resolve HEAD once per run, then pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed_chunked(commit: str) -> str:
    """Read the committed blob of chunked.py -- never the working copy."""
    proc = _git("show", f"{commit}:{_CHUNKED_PATH}")
    assert proc.returncode == 0, (
        f"cannot read committed {_CHUNKED_PATH}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _missing(blob: str) -> list[str]:
    """Return the labels of every pin absent from ``blob``."""
    labels = (
        "atomic guarded INSERT definition",
        "guarded INSERT use in create_session",
        "rowcount fail-closed + temp unlink",
    )
    return [label for label, marker in zip(labels, _ALL_MARKERS) if marker not in blob]


def _evaluate(blob: str) -> tuple[bool, str]:
    """Return ``(present, detail)`` for one candidate committed blob."""
    missing = _missing(blob)
    if not missing:
        return True, f"pin {_PIN_KEY!r} present in committed {_CHUNKED_PATH!r}"
    return False, (
        f"COMMITTED SECURITY FIX MARKER MISSING: {_PIN_KEY!r} -- missing from "
        f"committed blob {_CHUNKED_PATH!r}: {', '.join(missing)}; a stale staged "
        "copy has regressed the chunked-upload session cap and reopens the "
        "TOCTOU cap bypass (per-user sparse temp-file hoarding)"
    )


def test_committed_chunked_pins_atomic_insert(commit_sha: str) -> None:
    """The committed chunked.py still enforces the cap atomically."""
    blob = _committed_chunked(commit_sha)
    present, detail = _evaluate(blob)
    assert present, detail
    for marker in _ALL_MARKERS:
        assert blob.count(marker) == 1, (
            "each atomic-insert guard must appear exactly once in the committed blob"
        )


def test_committed_chunked_atomic_insert_non_vacuity() -> None:
    """A synthetic stale blob with the guards erased is flagged by the evaluator."""
    stale_blob = (
        "        active = self._db.fetch_one(SELECT_COUNT_SQL, (user_id, now))\n"
        "        if active['cnt'] >= max_active_sessions:\n"
        "            return None\n"
        "        cursor = self._db.execute(\n"
        "            'INSERT INTO media_upload_sessions VALUES (?, ?, ...)',\n"
        "            params,\n"
        "        )\n"
        "        return UploadSession(...)\n"
    )
    for marker in _ALL_MARKERS:
        assert marker not in stale_blob
    present, detail = _evaluate(stale_blob)
    assert not present, "synthetic stale bare-INSERT copy was not caught"
    assert _PIN_KEY in detail and "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_committed_chunked_markers_are_distinctive(commit_sha: str) -> None:
    """The pins are not ubiquitous strings: a naive walk does not contain them."""
    naive_walk = (
        "def create_session(...):\n"
        "    active = count_active_sessions(user_id)\n"
        "    if active >= limit:\n"
        "        return None\n"
        "    cursor = db.execute('INSERT INTO media_upload_sessions ...', params)\n"
        "    return UploadSession(...)\n"
    )
    for marker in _ALL_MARKERS:
        assert marker not in naive_walk
        assert marker in _committed_chunked(commit_sha)
