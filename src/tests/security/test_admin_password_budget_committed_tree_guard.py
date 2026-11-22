"""Committed-tree pin for the per-account admin password guessing budget.

The runtime guard in ``test_admin_password_per_account_budget.py`` reads the
working tree. A peer staging a stale copy of the admin auth modules could drop
the account budget while the working-tree test still sees the fixed files and
passes. This module reads the binding straight out of ``HEAD`` with ``git show``
so the pin travels with the committed tree, not the working copy.

Non-vacuity is proven two ways: synthetic stale blobs with the budget erased
are rejected by the same evaluator, and the originating fix commit (afc3a84b)
must be a real ancestor of HEAD.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_MIXIN_PATH = "src/core/admin/auth/auth_mixin.py"
_HELPERS_PATH = "src/core/admin/auth/helpers.py"
_CHECK_CALL = "if not _check_admin_password_budget(admin_id):"
_ACCOUNT_KEY = 'f"admin_login_account:{admin_id}"'
_RESET_CALL = 'reset_rate_limit(f"admin_login_account:{admin_id}")'
_FAIL_CLOSED = "return False"


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


def _mixin_has_budget(text: str) -> bool:
    return _CHECK_CALL in text and _RESET_CALL in text


def _helpers_has_budget(text: str) -> bool:
    return _ACCOUNT_KEY in text and _FAIL_CLOSED in text


@pytest.mark.security
def test_admin_password_budget_is_in_committed_tree(head_sha: str) -> None:
    """Both the key construction and the denial path must live in HEAD."""
    assert _mixin_has_budget(_committed_blob(head_sha, _MIXIN_PATH)), (
        "per-account budget call or success reset missing from committed "
        f"{_MIXIN_PATH!r}"
    )
    assert _helpers_has_budget(_committed_blob(head_sha, _HELPERS_PATH)), (
        "account-scoped limiter key or fail-closed denial missing from committed "
        f"{_HELPERS_PATH!r}"
    )


@pytest.mark.security
def test_budget_pin_is_non_vacuous() -> None:
    """A stale blob with the budget erased must be rejected by the evaluator."""
    stale_mixin = "        if not encryption.verify_password(password, password_hash):\n"
    assert not _mixin_has_budget(stale_mixin)
    fixed_mixin = stale_mixin.replace(
        "        if not encryption.verify_password",
        f"        {_CHECK_CALL}\n"
        "        if not encryption.verify_password",
    ) + _RESET_CALL
    assert _mixin_has_budget(fixed_mixin), "evaluator must accept the fixed form"

    stale_helpers = (
        "def _check_admin_password_budget(admin_id):\n"
        '    allowed, _ = check_rate_limit(f"admin_login:{admin_id}", 5, 900)\n'
        "    return bool(allowed)\n"
    )
    assert not _helpers_has_budget(stale_helpers)
    fixed_helpers = stale_helpers.replace(
        'f"admin_login:{admin_id}"', _ACCOUNT_KEY
    ).replace("    return bool(allowed)", f"    except Exception:\n        {_FAIL_CLOSED}\n    return bool(allowed)")
    assert _helpers_has_budget(fixed_helpers), "evaluator must accept the fail-closed form"


@pytest.mark.security
def test_fix_commit_is_ancestor(head_sha: str) -> None:
    """Pin that this is a follow-up to the real, reachable afc3a84b fix."""
    proc = _git("merge-base", "--is-ancestor", "afc3a84b", head_sha)
    assert proc.returncode == 0, "afc3a84b is not an ancestor of HEAD"
