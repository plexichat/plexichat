"""Committed-tree pin for the N691 artifact force-delete approval binding.

The AST guard in ``test_admin_artifact_approval_target_binding.py`` reads the
working tree.  If a peer stages a stale copy of ``admin/artifacts.py`` that
drops ``target_context=(aid, admin_id)``, the working-tree guard still sees the
fixed file and passes while the commit silently reverts the binding.  This
module reads the binding straight out of ``HEAD`` with ``git show`` so the pin
travels with the committed tree, not the working copy.

Non-vacuity is proven two ways: a synthetic stale blob with the binding erased
is rejected by the same evaluator, and the originating fix commit (f8575e44)
must be a real ancestor of HEAD.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BINDING_PATH = "src/api/routes/admin/artifacts.py"
_BINDING_MARKER = (
    'require_approved_request("artifacts.force_delete", target_context=(aid, admin_id))'
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
def head_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


def _committed_blob(sha: str, path: str) -> str:
    proc = _git("show", f"{sha}:{path}")
    assert proc.returncode == 0, (
        f"{path!r} not present in commit {sha}: {proc.stderr.strip()}"
    )
    return proc.stdout


def _binding_present(text: str) -> bool:
    return _BINDING_MARKER in text


def test_binding_present_in_committed_tree(head_sha: str) -> None:
    """The exact artifact+admin binding must live in the committed blob."""
    blob = _committed_blob(head_sha, _BINDING_PATH)
    assert _binding_present(blob), (
        "N691 binding missing from committed "
        f"{_BINDING_PATH!r}: expected {_BINDING_MARKER!r}"
    )


def test_binding_guard_is_non_vacuous() -> None:
    """A stale blob with only the action-only call must be rejected."""
    stale = '    require_approved_request("artifacts.force_delete")\n'
    assert _BINDING_MARKER not in stale
    assert not _binding_present(stale)

    fixed = stale.replace(
        'require_approved_request("artifacts.force_delete")', _BINDING_MARKER
    )
    assert _binding_present(fixed), "the evaluator must accept the fixed form"


def test_fix_commit_is_ancestor(head_sha: str) -> None:
    """Pin that this is a follow-up to the real, reachable f8575e44 fix."""
    proc = _git("merge-base", "--is-ancestor", "f8575e44", head_sha)
    assert proc.returncode == 0, "f8575e44 is not an ancestor of HEAD"
