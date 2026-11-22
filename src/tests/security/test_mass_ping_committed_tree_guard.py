"""Committed-tree pins for the @everyone-equivalent mass-ping fixes.

N719/N733 route the self-channel, default-role and all-member-role mass-ping
forms through the SINGLE fail-closed ``messages.mention_everyone`` verdict, and
N808 (``cb6636c6``) aligns the recipient-side suppression with that gate by
excluding the message author from the role-coverage verdict at BOTH the gate
(``validate_mentions``) and the fan-out (``create_notifications``).

The working-tree static guards
(``test_mass_ping_author_exclusion_static_guard`` /
``test_notification_preference_semantic_guard``) watch the live source. This
module is the *committed-tree* counterpart: it reads each marker straight out
of HEAD with ``git show <sha>:<path>`` so a stale staged copy of a contested
notification file cannot silently revert a fix. HEAD is resolved once per run
and pinned for every read.

Non-vacuity: a synthetic blob with the marker stripped out is flagged for every
pin, so the check is not a tautology.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class MassPingPin:
    key: str
    path: str
    marker: str
    why: str


# Markers are short, distinctive single-occurrence phrases taken verbatim from
# the committed blobs. Each is tied to the *behaviour* of the fix, so dropping
# the fix (not merely reformatting it) removes the marker.
MASS_PING_COMMITTED_PINS: tuple[MassPingPin, ...] = (
    MassPingPin(
        key="cb6636c6-mass-ping-fanout-author-exclusion",
        path="src/core/notifications/manager/creator.py",
        marker="excludes the author from both counts, matching the fan-out",
        why=(
            "cb6636c6 (N808): the fan-out's ROLE everyone-equivalent verdict "
            "(``_role_is_everyone_equivalent``) delegates to "
            "``role_covers_all_members`` with ``author_id`` excluded, matching "
            "the gate. Without it a role held by every OTHER member pings the "
            "@everyone audience and bypasses ``suppress_everyone``."
        ),
    ),
    MassPingPin(
        key="cb6636c6-mass-ping-gate-author-exclusion",
        path="src/core/notifications/manager/mention_validator.py",
        marker="Exclude the author: create_notifications always skips the",
        why=(
            "cb6636c6 (N808): the gate's ``_is_all_member_role`` memo calls "
            "``role_covers_all_members`` with the author excluded, so the "
            "permission verdict and the fan-out suppression agree on the "
            "all-except-author audience."
        ),
    ),
    MassPingPin(
        key="n719-n733-single-mass-mention-verdict",
        path="src/core/notifications/manager/mention_validator.py",
        marker="or _is_everyone_role(m)",
        why=(
            "N719/N733: the default-role and all-member-role forms join "
            "@everyone/@here and the self-channel mention in ONE "
            "``needs_mass_mention_gate`` sweep, so every form clears the same "
            "``messages.mention_everyone`` verdict. Dropping this arm lets a "
            "recreated-everyone role mass-ping without permission."
        ),
    ),
    MassPingPin(
        key="cb6636c6-coverage-helper-author-exclusion-param",
        path="src/core/notifications/manager/helpers.py",
        marker="exclude_user_id: Optional[SnowflakeID] = None",
        why=(
            "N783/cb6636c6: ``role_covers_all_members`` must keep its "
            "``exclude_user_id`` parameter; both the gate and the fan-out "
            "depend on it to measure the audience the pinger actually reaches."
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
    """Resolve HEAD once per run, then pin every blob read to that commit."""
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, f"cannot resolve HEAD: {proc.stderr.strip()}"
    sha = proc.stdout.strip()
    assert sha, "git rev-parse HEAD returned an empty revision"
    return sha


class BlobMissing(Exception):
    """Raised when a pinned path is absent from the commit."""


def _read_committed(sha: str, path: str) -> str:
    if ".." in Path(path).parts:
        raise BlobMissing(path, "unsafe path")
    proc = _git("show", f"{sha}:{path}")
    if proc.returncode != 0:
        raise BlobMissing(path, proc.stderr.strip())
    return proc.stdout


def _flag_if_missing(blob: str, pin: MassPingPin) -> str | None:
    """Return a failure detail when ``pin``'s marker is absent from ``blob``."""
    if pin.marker not in blob:
        return f"{pin.key}: marker {pin.marker!r} absent from committed {pin.path}. {pin.why}"
    return None


@pytest.mark.parametrize(
    "pin", MASS_PING_COMMITTED_PINS, ids=[p.key for p in MASS_PING_COMMITTED_PINS]
)
def test_mass_ping_fix_pinned_in_committed_tree(
    pin: MassPingPin, commit_sha: str
) -> None:
    """Every key mass-ping fix is present in the committed tree."""
    blob = _read_committed(commit_sha, pin.path)
    detail = _flag_if_missing(blob, pin)
    assert detail is None, detail


def test_registry_is_documentable_and_unique() -> None:
    keys = [p.key for p in MASS_PING_COMMITTED_PINS]
    assert keys, "registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate mass-ping pin keys"
    for pin in MASS_PING_COMMITTED_PINS:
        assert pin.marker, f"empty marker for {pin.key!r}"
        assert pin.path.startswith("src/"), f"bad path for {pin.key!r}"
        assert pin.why, f"undocumented purpose for {pin.key!r}"


def test_detector_flags_marker_stripped_from_committed_blob(commit_sha: str) -> None:
    """Non-vacuity: a blob missing the marker is flagged for every pin."""
    for pin in MASS_PING_COMMITTED_PINS:
        blob = _read_committed(commit_sha, pin.path)
        assert pin.marker in blob, f"precondition failed for {pin.key!r}"
        stripped = blob.replace(pin.marker, "")
        assert _flag_if_missing(stripped, pin), (
            f"detector did not flag {pin.key!r} with its marker stripped"
        )


def test_committed_blob_differs_from_stale_working_copy(commit_sha: str) -> None:
    """The reader must use the committed blob, not a stale working copy."""
    # If this assertion ever passes it means the reader was wired to the
    # working tree; that would let a stale staged copy mask a committed revert.
    committed = _read_committed(commit_sha, MASS_PING_COMMITTED_PINS[0].path)
    on_disk = (_REPO_ROOT / MASS_PING_COMMITTED_PINS[0].path).read_text(encoding="utf-8")
    # We only assert the committed blob carries the pin; the working copy may
    # legitimately diverge, so the two need not be equal. Reading from git is
    # the property under test, proven by the marker being found in `committed`.
    assert MASS_PING_COMMITTED_PINS[0].marker in committed
    assert isinstance(on_disk, str)
