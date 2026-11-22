"""Committed-tree guard: DSAR audit metadata is redacted before it is written.

Pins the fail-closed redaction call in ``src/core/dsar/audit_log.py`` to the
committed blob (read with ``git show``, never the working tree), so a stale
staged copy that restores raw ``metadata`` persistence is caught even while
the working copy still carries the fix.

Non-vacuity: a synthetic reverted blob is shown to be flagged, and the reader
is shown to return the committed blob rather than a divergent working copy.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET_PATH = "src/core/dsar/audit_log.py"

# The distinctive write-site block: metadata is redacted before it enters the
# entry that is serialized and checksummed. Pinning the full assignment (not
# just the import) catches a stale copy that keeps the import but writes the
# raw caller metadata again.
_WRITE_SITE_REDACTION = '"metadata": redact_secrets(metadata or {}),'

# The integrity-event hash must be computed over the same redacted payload.
_INTEGRITY_REDACTION = "payload = redact_secrets(dict(metadata))"

_MARKERS = (
    ("write-site-metadata-redaction", _WRITE_SITE_REDACTION),
    ("integrity-event-hash-redaction", _INTEGRITY_REDACTION),
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
def committed_blob() -> str:
    sha = _git("rev-parse", "HEAD")
    assert sha.returncode == 0, f"cannot resolve HEAD: {sha.stderr.strip()}"
    rev = sha.stdout.strip()
    assert rev, "git rev-parse HEAD returned an empty revision"
    proc = _git("show", f"{rev}:{_TARGET_PATH}")
    assert proc.returncode == 0, f"cannot read committed {_TARGET_PATH}: {proc.stderr}"
    return proc.stdout


@pytest.mark.parametrize("key,marker", _MARKERS, ids=[k for k, _ in _MARKERS])
def test_redaction_marker_present_at_head(committed_blob: str, key: str, marker: str):
    assert marker in committed_blob, (
        f"committed DSAR audit redaction marker {key!r} ({marker!r}) is missing "
        f"from {_TARGET_PATH}"
    )


def test_synthetic_reverted_blob_is_flagged():
    reverted = (
        "def log_event(...):\n"
        '    entry = {"metadata": metadata or {}}\n'
        "    payload = dict(metadata)\n"
    )
    for _key, marker in _MARKERS:
        assert marker not in reverted, f"synthetic revert still contains {marker!r}"


def test_reader_returns_committed_blob_not_working_tree(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "audit_log.py"
    target.write_text(_WRITE_SITE_REDACTION + "\n", encoding="utf-8")
    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "audit_log.py", cwd=repo).returncode == 0
    committed = _git(
        "-c",
        "user.email=guard@example.invalid",
        "-c",
        "user.name=guard",
        "commit",
        "-q",
        "-m",
        "guard",
        cwd=repo,
    )
    assert committed.returncode == 0, committed.stderr
    # Divergent working copy that erases the marker.
    target.write_text("# raw metadata persisted\n", encoding="utf-8")
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    blob = _git("show", f"{sha}:audit_log.py", cwd=repo).stdout
    assert _WRITE_SITE_REDACTION in blob
    assert _WRITE_SITE_REDACTION not in target.read_text(encoding="utf-8")
