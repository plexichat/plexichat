"""Committed-tree guard for edacde4c (N773): the automod violation/audit-log
read endpoints must never hand the offending message body to a moderator who
holds ``server.automod`` but is denied ``channels.view``/``messages.read`` on
the violating channel.

This is a standalone guard because the shared
``test_security_fix_marker_committed_tree_guard.py`` registry is a heavily
contested file: a peer's whole-file commit can drop an entry from it. This
module owns its own small registry and reads the blob straight out of ``HEAD``
(``git show HEAD:<path>``), never the working tree, so a stale staged copy of
``automod.py`` cannot mask a committed revert. Non-vacuity is proven twice:
each marker is falsifiable by removing just that text, and a realistic
pre-fix blob (gate removed, raw fields restored) is rejected by every marker.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_AUTOMOD = "src/api/routes/servers/automod.py"


@dataclass(frozen=True)
class FixMarker:
    key: str
    path: str
    marker: str
    why: str


_GATE_DEF = '_VIOLATION_BODY_PERMISSIONS = ("channels.view", "messages.read")\n\n\ndef _viewer_can_read_violation_body(\n    servers_mod, viewer_id, server_id, channel_id\n) -> bool:'
_FAIL_CLOSED = "    if not channel_id:\n        return False\n    try:\n        cid = int(channel_id)\n    except (TypeError, ValueError):\n        return False\n    if not cid:\n        return False\n    for permission in _VIOLATION_BODY_PERMISSIONS:\n        try:\n            if not servers_mod.has_permission(viewer_id, server_id, permission, cid):\n                return False\n        except Exception:\n            return False\n    return True"
_GATE_CALL = "    can_read = _viewer_can_read_violation_body(\n        servers_mod, viewer_id, server_id, violation.channel_id\n    )"
_MATCHED_REDACTED = (
    '        matched_content=violation.matched_content if can_read else "",'
)
_METADATA_REDACTED = "        metadata=(violation.metadata or {}) if can_read else {},"

FIX_MARKERS: tuple[FixMarker, ...] = (
    FixMarker(
        key="edacde4c-n773-violation-body-gate-def",
        path=_AUTOMOD,
        marker=_GATE_DEF,
        why=(
            "the per-channel view/read gate definition names BOTH required "
            "permissions; without it server.automod alone authorizes the body"
        ),
    ),
    FixMarker(
        key="edacde4c-n773-violation-body-fail-closed",
        path=_AUTOMOD,
        marker=_FAIL_CLOSED,
        why=(
            "missing/unparseable channel id, an unwired store and a raising "
            "permission lookup must all strip the body"
        ),
    ),
    FixMarker(
        key="edacde4c-n773-violation-body-gate-call",
        path=_AUTOMOD,
        marker=_GATE_CALL,
        why=(
            "the shared serializer must actually apply the gate to the "
            "violation's own channel; both endpoints route through it"
        ),
    ),
    FixMarker(
        key="edacde4c-n773-violation-body-content-redacted",
        path=_AUTOMOD,
        marker=_MATCHED_REDACTED,
        why="matched_content is blanked when the viewer cannot read the channel",
    ),
    FixMarker(
        key="edacde4c-n773-violation-body-metadata-redacted",
        path=_AUTOMOD,
        marker=_METADATA_REDACTED,
        why=(
            "content-bearing metadata is blanked too, so the body cannot leak "
            "via metadata.examples"
        ),
    ),
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
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    assert proc.stdout.strip(), "HEAD resolved to an empty revision"
    return proc.stdout.strip()


def _committed_blob(commit_sha: str, path: str = _AUTOMOD) -> str:
    proc = _git("show", f"{commit_sha}:{path}")
    assert proc.returncode == 0, (
        f"cannot read {path} from {commit_sha}: {proc.stderr.strip()}"
    )
    return proc.stdout


def test_registry_is_well_formed() -> None:
    keys = [r.key for r in FIX_MARKERS]
    assert len(keys) == 5
    assert len(set(keys)) == len(keys)
    for record in FIX_MARKERS:
        assert record.marker, f"empty marker for {record.key!r}"
        assert record.path == _AUTOMOD
        assert record.why, f"undocumented purpose for {record.key!r}"


@pytest.mark.parametrize("record", FIX_MARKERS, ids=[r.key for r in FIX_MARKERS])
def test_fix_marker_present_in_committed_tree(
    record: FixMarker, commit_sha: str
) -> None:
    blob = _committed_blob(commit_sha, record.path)
    assert record.marker in blob, (
        f"N773 FIX MARKER MISSING from committed {record.path}: {record.key!r} "
        f"-- {record.marker!r}. Purpose: {record.why}"
    )


@pytest.mark.parametrize("record", FIX_MARKERS, ids=[r.key for r in FIX_MARKERS])
def test_fix_marker_is_falsifiable(record: FixMarker, commit_sha: str) -> None:
    """Removing ONLY this marker from the real blob must be caught."""
    blob = _committed_blob(commit_sha, record.path)
    assert record.marker in blob, "precondition: marker must be live at HEAD"
    reverted = blob.replace(record.marker, "", 1)
    assert record.marker not in reverted
    assert all(
        other.marker in reverted for other in FIX_MARKERS if other.key != record.key
    ), "the mutation must be local to this marker"


def test_realistic_pre_fix_blob_is_rejected(commit_sha: str) -> None:
    """A gate reversion in the committed blob fails EVERY marker.

    This mirrors an actual revert: delete the gate and its call, and restore
    the two raw response fields. No marker may survive it vacuously.
    """
    blob = _committed_blob(commit_sha)
    reverted = (
        blob.replace(_GATE_DEF, "")
        .replace(_FAIL_CLOSED, "")
        .replace(_GATE_CALL, "")
        .replace(
            _MATCHED_REDACTED,
            "        matched_content=violation.matched_content,",
        )
        .replace(
            _METADATA_REDACTED,
            "        metadata=(violation.metadata or {}),",
        )
    )
    for record in FIX_MARKERS:
        assert record.marker not in reverted, (
            f"N773 fix marker {record.key!r} survived a realistic revert"
        )


def test_both_audit_read_endpoints_use_the_gated_serializer(commit_sha: str) -> None:
    blob = _committed_blob(commit_sha)
    assert blob.count("_violation_response(") == 3, (
        "expected the serializer definition plus exactly two endpoint call sites"
    )
    for handler, signature in (
        ("list", "async def get_server_automod_violations("),
        ("single", "async def get_server_automod_violation("),
    ):
        start = blob.index(signature)
        end = (
            blob.index("\n@router.", start)
            if "\n@router." in blob[start:]
            else len(blob)
        )
        body = blob[start:end]
        assert "_violation_response(" in body, (
            f"the {handler} automod violation read no longer uses the gated serializer"
        )
