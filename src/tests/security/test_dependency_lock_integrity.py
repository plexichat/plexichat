"""Offline regression gates for the compiled dependency locks.

The runtime and dev locks (``requirements.txt`` / ``requirements-dev.txt``)
are produced with ``uv pip compile --generate-hashes`` and installed with
``pip --require-hashes``.  That safety rests on invariants that are cheap to
check without network access:

1. every direct pin in an ``.in`` file is an exact ``name==version`` that
   appears at that exact version in its compiled lock, and every lock entry
   carries at least one ``--hash=sha256:`` digest (enforced by
   ``ci-tooling/check_pinned_deps.py``);
2. when both locks pin a distribution at the *same* version, their hash sets
   must agree -- a one-sided difference means one lock was regenerated against
   a different index or is stale;
3. the two standalone verification scripts stay green against the checked-in
   compose file.

These tests never contact PyPI; they pin the structural invariants only.  A
live verification that every hash set matches the digests published for its
pinned version is an out-of-band hardening check, not a CI gate.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_LOCK = _REPO_ROOT / "requirements.txt"
_DEV_LOCK = _REPO_ROOT / "requirements-dev.txt"

_PIN_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?==([^\s;\\]+)")
_HASH_RE = re.compile(r"^--hash=sha256:([0-9a-fA-F]{64})\s*\\?$")


def _parse_lock(path: Path) -> dict[str, tuple[str, frozenset[str]]]:
    """Return ``{normalized name: (version, {sha256 digests})}`` for a lock."""
    result: dict[str, tuple[str, frozenset[str]]] = {}
    current_name: str | None = None
    current_version: str | None = None
    hashes: set[str] = set()

    def close() -> None:
        if current_name is not None and current_version is not None:
            assert current_name not in result, (
                f"duplicate pin for {current_name} in {path}"
            )
            result[current_name] = (current_version, frozenset(hashes))

    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # Pins start at column 0; hash lines are indented continuation lines.
        pin = _PIN_RE.match(raw) if not raw.startswith((" ", "-")) else None
        if pin:
            close()
            current_name = re.sub(r"[-_.]+", "-", pin.group(1)).lower()
            current_version = pin.group(2)
            hashes = set()
            continue
        digest = _HASH_RE.match(stripped)
        if digest:
            assert current_name is not None, f"--hash without a pin above it in {path}"
            hashes.add(digest.group(1).lower())
    close()
    return result


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    path = _REPO_ROOT / script
    assert path.is_file(), f"missing verification script: {path}"
    return subprocess.run(
        [sys.executable, str(path), *args],
        cwd=str(_REPO_ROOT),
        text=True,
        capture_output=True,
        check=False,
    )


def test_pinned_deps_gate_passes() -> None:
    """Direct pins are exact, synced to the locks, and every entry is hashed."""
    result = _run("ci-tooling/check_pinned_deps.py", "--repo", str(_REPO_ROOT))
    assert result.returncode == 0, (
        "dependency pinning gate failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_every_lock_entry_is_hashed() -> None:
    """No distribution may ship in a lock without an artifact digest."""
    for path in (_RUNTIME_LOCK, _DEV_LOCK):
        for name, (_version, hashes) in _parse_lock(path).items():
            assert hashes, f"{path.name}: {name} has no --hash=sha256 entry"


def test_shared_versions_have_identical_hash_sets() -> None:
    """A distribution pinned at the same version in both locks must agree.

    Divergent hash sets for one version mean one lock's integrity metadata was
    edited or regenerated against a different source -- exactly the drift that
    turns ``--require-hashes`` into a false assurance.
    """
    runtime = _parse_lock(_RUNTIME_LOCK)
    dev = _parse_lock(_DEV_LOCK)

    shared = set(runtime) & set(dev)
    assert shared, "the two locks share no distributions; check the parser"
    for name in sorted(shared):
        r_version, r_hashes = runtime[name]
        d_version, d_hashes = dev[name]
        if r_version == d_version:
            assert r_hashes == d_hashes, (
                f"{name}=={r_version} has different hash sets in the two locks "
                f"(runtime-only: {sorted(r_hashes - d_hashes)}, "
                f"dev-only: {sorted(d_hashes - r_hashes)})"
            )


def test_compose_and_supply_chain_verifiers_pass() -> None:
    """The checked-in compose file must keep both pinning gates green."""
    for script in (
        "scripts/verify_compose_pinning.py",
        "scripts/verify_supply_chain.py",
    ):
        result = _run(script)
        assert result.returncode == 0, (
            f"{script} failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
