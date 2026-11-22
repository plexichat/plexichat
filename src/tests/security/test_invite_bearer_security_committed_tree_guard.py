"""Committed-tree pins for the N872 invite-bearer security invariants.

An invite code is a bearer credential: whoever holds it can join the server.
Two properties of the committed tree keep that credential from being
online-guessable, and both must survive a peer staging a stale whole file:

1. ``generate_invite_code`` draws from the CSPRNG with a hard 128-bit floor.
   ``_MIN_INVITE_CODE_BYTES = 16`` plus ``max(floor, configured)`` and a
   fail-closed ``except`` fallback mean a legacy short ``invite_code_length``
   can never weaken the code.

2. The redeem route spends one guess against the canonical-IP+user limiter
   *before* calling ``use_invite``. Without that ordering a wrong guess is
   free (no budget is consumed) and the online-guess protection is void.

Like the repository's other committed-tree guards, the reader fetches blobs
straight from the commit with ``git show <sha>:<path>`` -- never the working
tree -- so a stale staged copy cannot mask a committed revert. Non-vacuity is
proved twice: a synthetic blob with each marker erased is rejected by the same
evaluator, and the live-HEAD tie asserts the marker is really in the committed
blob.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

# Anchor 1a: the 128-bit floor constant and the generator that consumes it.
_INVITE_ENTROPY_FLOOR_CONSTANT = (
    "    _MIN_INVITE_CODE_BYTES = 16\n\n    def generate_invite_code(self) -> str:"
)

# Anchor 1b: clamp the configured length UP to the floor, and fail closed on a
# malformed/unusable configured value instead of falling back to a short code.
_INVITE_ENTROPY_FLOOR_CLAMP = (
    "            num_bytes = max(self._MIN_INVITE_CODE_BYTES, int(configured))\n"
    "        except (TypeError, ValueError):\n"
    "            num_bytes = self._MIN_INVITE_CODE_BYTES\n"
    "        while True:\n"
    "            code = secrets.token_urlsafe(num_bytes)"
)

# Anchor 2: the guess-limit call must immediately precede use_invite in the
# redeem route. Pinning the pair catches a dropped call, a reorder, or a second
# unguarded redemption site that a stale staged file could introduce.
_INVITE_REDEEM_GUESS_BEFORE_USE = (
    "                _enforce_invite_redeem_guess_limit(current_user.user_id, request)\n"
    "                result = servers_mod.use_invite(current_user.user_id, invite_code)"
)


@dataclass(frozen=True)
class InvitePin:
    """One committed-tree invariant that must remain in a fixed blob."""

    key: str
    path: str
    marker: str
    why: str


INVITE_PINS: tuple[InvitePin, ...] = (
    InvitePin(
        key="n872-invite-code-entropy-floor-constant",
        path="src/core/servers/handlers/members/invites.py",
        marker=_INVITE_ENTROPY_FLOOR_CONSTANT,
        why=(
            "an invite code is a bearer credential; _MIN_INVITE_CODE_BYTES=16 "
            "keeps the CSPRNG draw at >=128 bits so a legacy short "
            "invite_code_length cannot make it online-guessable"
        ),
    ),
    InvitePin(
        key="n872-invite-code-entropy-floor-clamp",
        path="src/core/servers/handlers/members/invites.py",
        marker=_INVITE_ENTROPY_FLOOR_CLAMP,
        why=(
            "max(floor, configured) with a fail-closed except fallback means a "
            "malformed or short configured length can never lower the entropy "
            "floor; without it a config value of 8 yields a weak code"
        ),
    ),
    InvitePin(
        key="n872-invite-redeem-guess-limit-before-use",
        path="src/api/routes/channels/invites.py",
        marker=_INVITE_REDEEM_GUESS_BEFORE_USE,
        why=(
            "the redeem route must spend a guess against the canonical-IP+user "
            "limiter BEFORE use_invite; otherwise wrong guesses are free and "
            "the shared budget never throttles an online brute force"
        ),
    ),
)

INVITE_PIN_KEYS: frozenset[str] = frozenset(pin.key for pin in INVITE_PINS)


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=_REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _committed_blob(commit: str, path: str) -> str:
    proc = _git("show", f"{commit}:{path}")
    assert proc.returncode == 0, f"{path!r} missing from commit: {proc.stderr.strip()}"
    return proc.stdout


def _evaluate(pin: InvitePin, reader) -> tuple[bool, str]:
    """Return ``(present, detail)`` using an injectable path -> blob reader."""
    try:
        blob = reader(pin.path)
    except (AssertionError, OSError, UnicodeDecodeError) as exc:
        return False, f"{pin.path!r} unreadable from commit: {exc}"
    if pin.marker in blob:
        return True, f"marker {pin.key!r} present in committed blob {pin.path!r}"
    return False, (
        f"COMMITTED SECURITY FIX MARKER MISSING: {pin.key!r} -- "
        f"{pin.marker} not found in committed blob {pin.path!r}. "
        f"Purpose: {pin.why}"
    )


def test_invite_pin_registry_is_well_formed() -> None:
    """No empty/duplicate pins; the key set names exactly the registered pins."""
    keys = [pin.key for pin in INVITE_PINS]
    assert keys, "invite pin registry must not be empty"
    assert len(keys) == len(set(keys)), "duplicate invite pin keys"
    assert INVITE_PIN_KEYS == set(keys)
    assert len(INVITE_PIN_KEYS) == 3
    assert INVITE_PIN_KEYS == frozenset(
        {
            "n872-invite-code-entropy-floor-constant",
            "n872-invite-code-entropy-floor-clamp",
            "n872-invite-redeem-guess-limit-before-use",
        }
    )
    for pin in INVITE_PINS:
        assert pin.marker, f"empty marker for {pin.key!r}"
        assert pin.path.startswith("src/"), f"bad path for {pin.key!r}"


@pytest.mark.parametrize("pin", INVITE_PINS, ids=[p.key for p in INVITE_PINS])
def test_invite_pin_present_in_committed_tree(pin: InvitePin, commit_sha: str) -> None:
    """Each invariant is really in the committed blob at HEAD (live-HEAD tie)."""
    present, detail = _evaluate(pin, lambda path: _committed_blob(commit_sha, path))
    assert present, detail


@pytest.mark.parametrize("pin", INVITE_PINS, ids=[p.key for p in INVITE_PINS])
def test_invite_pin_non_vacuous(pin: InvitePin) -> None:
    """A blob with the marker erased is rejected by the same evaluator."""

    def reverted_blob(path: str) -> str:
        return f"# synthetic reverted blob for {path}\n# fix marker erased\n"

    present, detail = _evaluate(pin, reverted_blob)
    assert not present, f"synthetic committed revert of {pin.key!r} was not caught"
    assert pin.key in detail and pin.marker in detail
    assert "COMMITTED SECURITY FIX MARKER MISSING" in detail
