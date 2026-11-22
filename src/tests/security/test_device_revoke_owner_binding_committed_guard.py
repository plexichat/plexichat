"""Committed-tree pin for the device-revoke session owner binding.

The behavioural guard in ``test_device_revoke_session_owner_binding.py`` reads
the working tree.  A peer staging a stale ``managers/device.py`` that drops the
``user_id`` predicate on the session revocation would still satisfy that guard
while the commit silently re-opens the cross-account blast radius.  This module
reads both statements straight out of ``HEAD`` with ``git show`` so the pin
travels with the committed tree, not the working copy.

Non-vacuity: a stale blob with the unbound SQL is rejected by the same
evaluator, and the fixed forms are accepted.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIX_PATH = "src/core/auth/managers/device.py"

# Both the socket-owner capture and the revoke write must carry the owner
# predicate. Pinning only the UPDATE would let the socket path regress.
_SOCKET_QUERY_MARKER = (
    "SELECT id FROM auth_sessions WHERE device_id = ? AND user_id = ? AND revoked = 0"
)
_REVOKE_WRITE_MARKER = (
    "UPDATE auth_sessions SET revoked = 1 WHERE device_id = ? AND user_id = ?"
)


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
    )


def test_head_sha_resolves() -> str:
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
    return _SOCKET_QUERY_MARKER in text and _REVOKE_WRITE_MARKER in text


def test_owner_binding_present_in_committed_tree() -> None:
    sha = test_head_sha_resolves()
    blob = _committed_blob(sha, _FIX_PATH)
    assert _binding_present(blob), (
        "device-revoke session owner binding missing from committed "
        f"{_FIX_PATH!r}: expected both {_SOCKET_QUERY_MARKER!r} and "
        f"{_REVOKE_WRITE_MARKER!r}"
    )


def test_owner_binding_guard_is_non_vacuous() -> None:
    stale = (
        '"SELECT id FROM auth_sessions WHERE device_id = ? AND revoked = 0",\n'
        '"UPDATE auth_sessions SET revoked = 1 WHERE device_id = ?", (device_id,)'
    )
    assert not _binding_present(stale)

    fixed = stale.replace(
        "device_id = ? AND revoked = 0",
        "device_id = ? AND user_id = ? AND revoked = 0",
    ).replace(
        'UPDATE auth_sessions SET revoked = 1 WHERE device_id = ?", (device_id,)',
        'UPDATE auth_sessions SET revoked = 1 WHERE device_id = ? AND user_id = ?", '
        "(device_id, user_id)",
    )
    assert _binding_present(fixed), "the evaluator must accept the fixed form"
