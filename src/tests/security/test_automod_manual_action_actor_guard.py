"""Committed-tree guard for the manual-action actor binding.

A stale private-index commit can stage an older copy of
``src/core/automod/manager/actions.py`` and silently remove the line that binds
the executor actor to the permission-checked moderator.  The behavioural
regression test catches that at test time; this guard catches it directly in
the committed blob, independently of whatever the working tree happens to
contain.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/automod/manager/actions.py"
_MARKER = '            context["bot_user_id"] = user_id\n'


def _committed_blob() -> str:
    return subprocess.check_output(
        ["git", "show", f"HEAD:{_TARGET}"], cwd=_REPO_ROOT, text=True
    )


def _binding_present(blob: str) -> bool:
    return blob.count(_MARKER) == 1


def test_manual_action_actor_binding_is_in_committed_tree() -> None:
    assert _binding_present(_committed_blob()), (
        f"{_TARGET} at HEAD must bind the executor actor to the "
        "permission-checked moderator exactly once"
    )


def test_guard_detects_a_stale_revert() -> None:
    """Non-vacuity: stripping the binding from the blob must be flagged."""
    stale = _committed_blob().replace(_MARKER, "")
    assert _MARKER not in stale
    assert _binding_present(stale) is False
