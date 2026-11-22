"""Committed-tree guard: webhook token stays redacted in exception-handler logs.

dfb27bf5 fixed a real credential leak: the FastAPI exception handlers in
``src/api/middleware/error_handling.py`` logged ``request.url.path`` verbatim.
On a rejected ``POST /api/v1/webhooks/{webhook_id}/{token}`` execution (for
example an oversized body that raises ``RequestValidationError``) that resolved
path carries the LIVE webhook token, so every failed delivery wrote the secret
to the log sink, bypassing the access-log redactor.

The fix routes those two warning logs through ``_safe_log_path`` (the same
redactor the access-log middleware uses), which fails closed: when routing
metadata is present it emits the route template (``.../{token}``), and when it
is absent it masks the token segment directly.

This guard reads the blob out of the committed tree with ``git show`` -- never
the working tree -- so a stale staged whole-file copy that restores
``request.url.path`` is caught even while the working copy still carries the
fix.  Non-vacuity is proven three ways: a synthetic blob with the redaction
erased is flagged; the reader is shown to return the committed blob rather than
a divergent working copy; and the registry is checked for well-formedness.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET_PATH = "src/api/middleware/error_handling.py"

# The distinctive validation-error handler block added by dfb27bf5.  Pinning
# the multi-line call (not just the helper name) means a stale copy that keeps
# the import but logs the raw ``request.url.path`` again is caught by name.
_VALIDATION_ERROR_REDACTION = (
    "        logger.warning(\n"
    '            f"Validation error on {request.method} "\n'
    '            f"{_safe_log_path(request.scope, request.url.path)}: "\n'
    '            f"{_redact_validation_error_inputs(errors)}"\n'
    "        )"
)

# The security-rejection handler was fixed in the same commit; pin it too so a
# partial revert that fixes only one of the two handlers is caught.
_SECURITY_REJECTION_REDACTION = (
    "        logger.warning(\n"
    '            f"Security rejection on {request.method} "\n'
    '            f"{_safe_log_path(request.scope, request.url.path)}: "\n'
    '            f"{type(exc).__name__} code={exc.code} detail={exc}"\n'
    "        )"
)


@dataclass(frozen=True)
class RedactionMarker:
    """One committed-tree pin for the webhook-token log-redaction fix."""

    key: str
    path: str
    marker: str
    why: str


COMMITTED_REDACTION_MARKERS: tuple[RedactionMarker, ...] = (
    RedactionMarker(
        key="dfb27bf5-validation-error-webhook-token-redaction",
        path=_TARGET_PATH,
        marker=_VALIDATION_ERROR_REDACTION,
        why=(
            "dfb27bf5: the RequestValidationError handler logged request.url.path "
            "verbatim; a rejected webhook execution leaks the live token in that "
            "path. Routing it through _safe_log_path fails closed; a stale staged "
            "copy that restores the raw path reopens the leak on every failure."
        ),
    ),
    RedactionMarker(
        key="dfb27bf5-security-rejection-webhook-token-redaction",
        path=_TARGET_PATH,
        marker=_SECURITY_REJECTION_REDACTION,
        why=(
            "dfb27bf5: the SecurityRejection handler is the sibling log site fixed "
            "in the same commit; pinning it independently catches a partial revert "
            "that leaves this handler still writing the resolved token path."
        ),
    ),
)

PIN_KEYS: frozenset[str] = frozenset(m.key for m in COMMITTED_REDACTION_MARKERS)


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


def _evaluate(record: RedactionMarker, reader) -> tuple[bool, str]:
    try:
        text = reader(record.path)
    except (BlobMissing, OSError, UnicodeDecodeError) as exc:
        return False, (
            f"COMMITTED WEBHOOK-LOG REDACTION MARKER MISSING: {record.key!r} -- "
            f"{record.path!r} unreadable in commit ({exc}). Purpose: {record.why}"
        )
    if record.marker in text:
        return True, f"marker for {record.key!r} present in committed {record.path!r}"
    return False, (
        f"COMMITTED WEBHOOK-LOG REDACTION MARKER MISSING: {record.key!r} -- "
        f"distinctive redaction block {record.marker} not found in committed "
        f"{record.path!r}. Purpose: {record.why}"
    )


@pytest.mark.parametrize(
    "record",
    COMMITTED_REDACTION_MARKERS,
    ids=[m.key for m in COMMITTED_REDACTION_MARKERS],
)
def test_redaction_marker_present_at_head(
    record: RedactionMarker, commit_sha: str
) -> None:
    """Every pinned redaction site is present in the committed blob."""
    present, detail = _evaluate(record, _make_blob_reader(commit_sha))
    assert present, detail


def test_registry_is_well_formed() -> None:
    """The registry is non-empty, unique, documented and mine-only."""
    keys = [m.key for m in COMMITTED_REDACTION_MARKERS]
    assert keys and len(keys) == len(set(keys))
    assert PIN_KEYS == set(keys)
    for record in COMMITTED_REDACTION_MARKERS:
        assert record.marker and record.path == _TARGET_PATH and record.why


@pytest.mark.parametrize(
    "record",
    COMMITTED_REDACTION_MARKERS,
    ids=[m.key for m in COMMITTED_REDACTION_MARKERS],
)
def test_synthetic_reverted_blob_is_flagged(record: RedactionMarker) -> None:
    """Non-vacuity: a stale blob with the redaction erased must be flagged."""

    def reverted_blob(path: str) -> str:
        return (
            f"# stale exception handler for {path}\n# raw request.url.path restored\n"
        )

    present, detail = _evaluate(record, reverted_blob)
    assert not present, f"synthetic revert of {record.key!r} was not caught"
    assert record.key in detail and record.marker in detail
    assert "COMMITTED WEBHOOK-LOG REDACTION MARKER MISSING" in detail


def test_reader_returns_committed_blob_not_stale_working_tree(tmp_path: Path) -> None:
    """Non-vacuity: the reader really reads HEAD, not a divergent working copy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "handler.py"
    target.write_text(_VALIDATION_ERROR_REDACTION + "\n", encoding="utf-8")
    assert _git("init", "-q", cwd=repo).returncode == 0
    assert _git("add", "handler.py", cwd=repo).returncode == 0
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

    # Peer-style stale working copy: the redaction is gone from disk only.
    target.write_text("# raw request.url.path restored\n", encoding="utf-8")
    assert _VALIDATION_ERROR_REDACTION not in target.read_text(encoding="utf-8")

    record = RedactionMarker(
        key="tmp-committed-vs-working",
        path="handler.py",
        marker=_VALIDATION_ERROR_REDACTION,
        why="self-test only",
    )
    present, detail = _evaluate(record, _make_blob_reader(sha, repo_root=repo))
    assert present, detail
