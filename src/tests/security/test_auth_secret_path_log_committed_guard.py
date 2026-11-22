"""Committed-tree guard: failed-auth logs never write a secret request path.

903cea05 fixed a real leak: ``get_current_user`` logged ``request.url.path``
verbatim at DEBUG, so an unauthenticated ``DELETE
/api/v1/features/push/tokens/{token_value}`` wrote the live push token to the
log sink, bypassing the access-log redactor.  The fix routes that log line
through ``_safe_log_path``.

A follow-up probe found the SAME bug class one layer earlier:
``AuthenticationMiddleware`` logs failed-auth paths BEFORE routing (no
``scope["route"]``), so ``_safe_log_path`` used to return the raw path and the
middleware's DEBUG/WARNING logs still leaked the push token on a rejected
bearer.  The residual fix (a) teaches ``_safe_log_path`` to mask the
push-token segment when routing metadata is absent and (b) routes every
middleware path log through the precomputed ``log_path``.

This guard reads blobs out of the committed tree with ``git show`` -- never the
working tree -- so a stale staged whole-file copy that restores a verbatim
``request.url.path`` log is caught.  Non-vacuity is proven three ways: a
synthetic reverted blob is flagged; the reader is shown to return the committed
blob rather than a divergent working copy; and the registry is checked for
well-formedness.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTH_PATH = "src/api/middleware/authentication.py"
_LOGGING_PATH = "src/api/middleware/logging.py"

# 903cea05: the canary regression's primary fix -- get_current_user's DEBUG
# line.  Pin the multi-line call, not just the helper name, so a stale copy that
# keeps the import but restores the raw path is caught by name.
_GET_CURRENT_USER_REDACTION = (
    '        logger.debug(\n'
    '            f"get_current_user: No user in state for path "\n'
    '            f"{_safe_log_path(request.scope, request.url.path)}. Error: {error}"\n'
    '        )'
)

# Residual fix: the pre-routing middleware now computes a single safe path and
# uses it for ALL its path logs.  The comment is pinned too so a stale copy that
# keeps the helper but reverts the call sites is caught.
_MIDDLEWARE_SAFE_PATH = (
    '            path = scope.get("path", "")\n'
    '            # SECURITY: AuthenticationMiddleware runs before routing, so\n'
    '            # scope["route"] is absent here. _safe_log_path still fails closed\n'
    '            # for credential-bearing paths (webhook token, invite code, push\n'
    '            # token) by masking the secret segment. Use this for ALL path\n'
    '            # logs below; never interpolate the raw resolved ``path``.\n'
    '            log_path = _safe_log_path(scope, path)'
)

# The exact site that leaked in the probe.  Pinning it independently catches a
# partial revert that fixes only get_current_user but leaves this DEBUG line
# writing the resolved token path.
_MIDDLEWARE_FAILED_AUTH_REDACTION = (
    '                                logger.debug(\n'
    '                                    f"Authentication failed for path {log_path}: {e}"\n'
    '                                )'
)

# _safe_log_path's no-route-metadata branch must know about push tokens,
# otherwise the two pins above would still emit the raw credential.
_PUSH_TOKEN_SEGMENT_REDACTION = (
    '    if len(parts) >= 4 and parts[-3] == "push" and parts[-2] == "tokens":\n'
    '        return "/".join(parts[:-1]) + "/{token_value}"'
)


@dataclass(frozen=True)
class AuthPathMarker:
    """One committed-tree pin for failed-auth secret-path log redaction."""

    key: str
    path: str
    marker: str
    why: str


COMMITTED_AUTH_PATH_MARKERS: tuple[AuthPathMarker, ...] = (
    AuthPathMarker(
        key="903cea05-get-current-user-secret-path-redaction",
        path=_AUTH_PATH,
        marker=_GET_CURRENT_USER_REDACTION,
        why=(
            "903cea05: get_current_user logged the resolved path verbatim on "
            "failed auth; a rejected push-token delete wrote the live token to "
            "the sink. Routing it through _safe_log_path fails closed."
        ),
    ),
    AuthPathMarker(
        key="n814-residual-middleware-safe-path",
        path=_AUTH_PATH,
        marker=_MIDDLEWARE_SAFE_PATH,
        why=(
            "AuthenticationMiddleware logs before routing with no scope[route]; "
            "it must compute one _safe_log_path-derived log_path and use it for "
            "every path log so a rejected bearer cannot leak the credential."
        ),
    ),
    AuthPathMarker(
        key="n814-residual-middleware-failed-auth-log",
        path=_AUTH_PATH,
        marker=_MIDDLEWARE_FAILED_AUTH_REDACTION,
        why=(
            "This is the exact DEBUG site the canary probe caught leaking the "
            "push token; pinning it independently catches a partial revert that "
            "fixes only get_current_user."
        ),
    ),
    AuthPathMarker(
        key="n814-residual-safe-log-path-push-token",
        path=_LOGGING_PATH,
        marker=_PUSH_TOKEN_SEGMENT_REDACTION,
        why=(
            "_safe_log_path's fallback only masked webhook/invite segments; "
            "without this push-token rule the pre-routing middleware pins would "
            "still emit the raw live token."
        ),
    ),
)

PIN_KEYS: frozenset[str] = frozenset(m.key for m in COMMITTED_AUTH_PATH_MARKERS)
_TARGET_PATHS: frozenset[str] = frozenset(m.path for m in COMMITTED_AUTH_PATH_MARKERS)


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


def _evaluate(record: AuthPathMarker, reader) -> tuple[bool, str]:
    try:
        text = reader(record.path)
    except (BlobMissing, OSError, UnicodeDecodeError) as exc:
        return False, (
            f"COMMITTED AUTH-PATH REDACTION MARKER MISSING: {record.key!r} -- "
            f"{record.path!r} unreadable in commit ({exc}). Purpose: {record.why}"
        )
    if record.marker in text:
        return True, f"marker for {record.key!r} present in committed {record.path!r}"
    return False, (
        f"COMMITTED AUTH-PATH REDACTION MARKER MISSING: {record.key!r} -- "
        f"distinctive redaction block {record.marker} not found in committed "
        f"{record.path!r}. Purpose: {record.why}"
    )


@pytest.mark.parametrize(
    "record",
    COMMITTED_AUTH_PATH_MARKERS,
    ids=[m.key for m in COMMITTED_AUTH_PATH_MARKERS],
)
def test_redaction_marker_present_at_head(
    record: AuthPathMarker, commit_sha: str
) -> None:
    """Every pinned redaction site is present in the committed blob."""
    present, detail = _evaluate(record, _make_blob_reader(commit_sha))
    assert present, detail


def test_registry_is_well_formed() -> None:
    """The registry is non-empty, unique, documented and scoped to our files."""
    keys = [m.key for m in COMMITTED_AUTH_PATH_MARKERS]
    assert keys and len(keys) == len(set(keys))
    assert PIN_KEYS == set(keys)
    for record in COMMITTED_AUTH_PATH_MARKERS:
        assert record.marker and record.path in _TARGET_PATHS and record.why


@pytest.mark.parametrize(
    "record",
    COMMITTED_AUTH_PATH_MARKERS,
    ids=[m.key for m in COMMITTED_AUTH_PATH_MARKERS],
)
def test_synthetic_reverted_blob_is_flagged(record: AuthPathMarker) -> None:
    """Non-vacuity: a stale blob with the redaction erased must be flagged."""

    def reverted_blob(path: str) -> str:
        return f"# stale middleware for {path}\n# raw request.url.path restored\n"

    present, detail = _evaluate(record, reverted_blob)
    assert not present, f"synthetic revert of {record.key!r} was not caught"
    assert record.key in detail and record.marker in detail
    assert "COMMITTED AUTH-PATH REDACTION MARKER MISSING" in detail


def test_reader_returns_committed_blob_not_stale_working_tree(tmp_path: Path) -> None:
    """Non-vacuity: the reader really reads HEAD, not a divergent working copy."""
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "handler.py"
    target.write_text(_GET_CURRENT_USER_REDACTION + "\n", encoding="utf-8")
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

    target.write_text("# raw request.url.path restored\n", encoding="utf-8")
    assert _GET_CURRENT_USER_REDACTION not in target.read_text(encoding="utf-8")

    record = AuthPathMarker(
        key="tmp-committed-vs-working",
        path="handler.py",
        marker=_GET_CURRENT_USER_REDACTION,
        why="self-test only",
    )
    present, detail = _evaluate(record, _make_blob_reader(sha, repo_root=repo))
    assert present, detail
