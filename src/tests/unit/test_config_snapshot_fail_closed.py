"""Fail-closed loading for the versioned config snapshot.

A failed rebuild must not leave a stale value cached with a freshly advanced
staleness-ceiling timestamp: the ceiling would then keep serving that stale
config (which can gate authorization/routing) for the whole window instead of
retrying the load.  The counting harness below makes the before/after explicit:

    before fix: 2 loader calls; the 3rd access serves the stale value silently
    after fix:  3 loader calls; no stale value is ever served
"""

from __future__ import annotations

import pytest

from src.core.config.snapshot import VersionedConfigSnapshot


def test_failed_rebuild_does_not_serve_stale_value_inside_ceiling():
    version = ["v1"]
    calls: list[int] = []
    fail = [True]

    def loader():
        calls.append(1)
        if fail[0]:
            raise RuntimeError("transient parse failure")
        return {"value": version[0]}

    snap = VersionedConfigSnapshot(
        loader, version_fn=lambda: tuple(version), max_age=60.0
    )

    # Cold build succeeds.
    fail[0] = False
    assert snap.snapshot() == {"value": "v1"}
    assert len(calls) == 1

    # Source changes and the ceiling elapses, forcing a rebuild that fails.
    snap._version_checked_at -= 61.0
    version[0] = "v2"
    fail[0] = True
    with pytest.raises(RuntimeError, match="transient"):
        snap.snapshot()
    assert len(calls) == 2

    # The next access is still inside the 60s ceiling. The failed check must
    # not have armed the ceiling over the stale v1 value: the loader is retried
    # instead of the hot path short-circuiting to stale memory.
    with pytest.raises(RuntimeError, match="transient"):
        snap.snapshot()
    assert len(calls) == 3, "a failed rebuild must be retried, not masked by TTL"

    # Once the loader recovers, the new version is materialised immediately.
    fail[0] = False
    assert snap.snapshot() == {"value": "v2"}
    assert snap.version == ("v2",)
    assert len(calls) == 4


def test_failed_rebuild_clears_version_and_ceiling_clock():
    version = ["v1"]
    fail = [True]

    def loader():
        if fail[0]:
            raise RuntimeError("parse failed")
        return {"value": version[0]}

    snap = VersionedConfigSnapshot(
        loader, version_fn=lambda: tuple(version), max_age=60.0
    )
    fail[0] = False
    snap.snapshot()
    assert snap.version == ("v1",)
    assert snap.stats()["version_checks"] == 1

    snap._version_checked_at -= 61.0
    version[0] = "v2"
    fail[0] = True
    with pytest.raises(RuntimeError):
        snap.snapshot()

    assert snap.version is None, "a failed rebuild must not keep the old version"
    assert snap._version_checked_at is None, "the ceiling clock must be reset"
