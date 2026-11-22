"""Committed-tree guard: invite guessing cannot inherit the fail-open override.

``check_rate_limit`` honours the operator-wide
``rate_limiting.fail_open_on_backend_error`` escape hatch on a limiter backend
fault. The invite redemption guess budget is a bearer-credential guard, so the
``invites:redeem-guess:`` key namespace is registered as fail-closed: a backend
fault denies for those keys regardless of the global override. The fault branch
must consult that registry BEFORE it ever reads the override config.

The behavioural test proves the runtime property; this guard reads the limiter
blob straight from the commit with ``git show`` -- never the working tree -- so
a stale staged copy cannot silently revert the fix. Non-vacuity is shown by
reverting each marker one at a time and asserting the evaluator rejects it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security, pytest.mark.unit]

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TARGET = "src/core/database/cache/rate_limit.py"

# The fail-closed namespace registry must include the invite guess budget.
_FAIL_CLOSED_REGISTRY = '_FAIL_CLOSED_KEY_PREFIXES = ("invites:redeem-guess:",)'

# The fault branch must gate the global fail-open config read on the namespace
# check, so a config-only change cannot re-enable online invite-code guessing.
_NAMESPACE_GATE = (
    "        fail_open = not key.startswith(_FAIL_CLOSED_KEY_PREFIXES)\n"
    "        if fail_open:\n"
)


@dataclass(frozen=True)
class Pin:
    key: str
    path: str
    marker: str
    why: str


_PINS: tuple[Pin, ...] = (
    Pin(
        key="invite-guess-fail-closed-namespace-registered",
        path=_TARGET,
        marker=_FAIL_CLOSED_REGISTRY,
        why=(
            "the invite redemption guess keys share the "
            "'invites:redeem-guess:' prefix; that namespace must be registered "
            "as fail-closed or the global fail_open_on_backend_error override "
            "can reopen online brute force of invite codes with a config line"
        ),
    ),
    Pin(
        key="invite-guess-fail-open-override-namespace-gated",
        path=_TARGET,
        marker=_NAMESPACE_GATE,
        why=(
            "the fault branch must consult the fail-closed namespace registry "
            "before reading the global override; an ungated config read would "
            "let the override weaken the invite guess budget"
        ),
    ),
)


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd or _REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture(scope="module")
def commit_sha() -> str:
    proc = _git("rev-parse", "HEAD")
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _committed(commit: str, path: str, cwd: Path | None = None) -> str:
    proc = _git("show", f"{commit}:{path}", cwd=cwd)
    assert proc.returncode == 0, f"{path!r} missing from commit: {proc.stderr.strip()}"
    return proc.stdout


def _evaluate(pin: Pin, reader) -> tuple[bool, str]:
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


def test_pin_registry_is_well_formed() -> None:
    keys = [pin.key for pin in _PINS]
    assert keys and len(keys) == len(set(keys)), "duplicate or empty pin keys"
    assert len(keys) == 2
    for pin in _PINS:
        assert pin.marker, f"empty marker for {pin.key!r}"
        assert pin.path.startswith("src/"), f"bad path for {pin.key!r}"


@pytest.mark.parametrize("pin", _PINS, ids=[p.key for p in _PINS])
def test_pin_present_in_committed_tree(pin: Pin, commit_sha: str) -> None:
    present, detail = _evaluate(pin, lambda path: _committed(commit_sha, path))
    assert present, detail


@pytest.mark.parametrize("pin", _PINS, ids=[p.key for p in _PINS])
def test_pin_non_vacuous(pin: Pin) -> None:
    def reverted_blob(path: str) -> str:
        return f"# synthetic reverted blob for {path}\n# fix marker erased\n"

    present, detail = _evaluate(pin, reverted_blob)
    assert not present, f"synthetic committed revert of {pin.key!r} was not caught"
    assert pin.key in detail and pin.marker in detail
    assert "COMMITTED SECURITY FIX MARKER MISSING" in detail


def test_reader_uses_committed_blob_not_working_tree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert _git("init", "-q", cwd=repo).returncode == 0
    target = repo / "module.py"
    target.write_text("VALUE = 'committed'\n", encoding="utf-8")
    assert _git("add", "module.py", cwd=repo).returncode == 0
    assert (
        _git(
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "commit",
            "-qm",
            "init",
            cwd=repo,
        ).returncode
        == 0
    )
    sha = _git("rev-parse", "HEAD", cwd=repo).stdout.strip()
    target.write_text("VALUE = 'working-tree-drift'\n", encoding="utf-8")
    assert _committed(sha, "module.py", cwd=repo) == "VALUE = 'committed'\n"
