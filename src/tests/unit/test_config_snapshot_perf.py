"""Focused tests for the versioned config snapshot used by API config.

Before this module, config consumers used a wall-clock TTL: every expiry paid
the full read/validate cost even for an unchanged file, and a real edit stayed
invisible for the rest of the TTL. These tests pin the replacement contract:
the loader runs once per *version*, an mtime change is observed immediately,
and an explicit invalidate forces a fresh read.
"""

import os
import time

from src.core.config.snapshot import VersionedConfigSnapshot, file_version


def _counter_loader(calls, value):
    def loader():
        calls.append(value)
        return {"value": value, "reads": len(calls)}

    return loader


def test_snapshot_reuses_value_until_version_changes(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("a: 1\n")
    calls = []
    snapshot = VersionedConfigSnapshot(
        _counter_loader(calls, "first"), paths=[config_path]
    )

    first = snapshot.snapshot()
    second = snapshot.snapshot()
    third = snapshot.snapshot()

    assert first == second == third
    assert len(calls) == 1, "unchanged source must not be re-read"
    assert snapshot.stats()["hits"] == 2
    assert snapshot.stats()["misses"] == 1
    assert snapshot.stats()["rebuilds"] == 1


def test_snapshot_invalidates_on_mtime_change(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("a: 1\n")
    calls = []
    snapshot = VersionedConfigSnapshot(
        _counter_loader(calls, "v1"), paths=[config_path]
    )

    assert snapshot.get("value") == "v1"
    old_version = snapshot.version
    assert old_version == file_version(config_path)

    # Simulate a config edit without changing file size, proving the version
    # is based on mtime (not size or a timer).
    future = time.time() + 5
    os.utime(config_path, (future, future))

    assert snapshot.version == old_version  # still cached until accessed
    assert file_version(config_path) != old_version
    assert snapshot.get("value") == "v1"  # producer still returns v1, but...
    assert len(calls) == 2, "changed mtime must force exactly one rebuild"
    assert snapshot.stats()["rebuilds"] == 2


def test_snapshot_missing_file_then_created_rebuilds(tmp_path):
    config_path = tmp_path / "new-config.yaml"
    calls = []
    snapshot = VersionedConfigSnapshot(
        _counter_loader(calls, "created"), paths=[config_path]
    )

    snapshot.snapshot()
    assert len(calls) == 1
    assert snapshot.version == (None,)

    config_path.write_text("a: 1\n")
    snapshot.snapshot()
    assert len(calls) == 2, "file creation changes the version"


def test_explicit_invalidate_forces_rebuild(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text("a: 1\n")
    calls = []
    snapshot = VersionedConfigSnapshot(_counter_loader(calls, "x"), paths=[config_path])

    snapshot.snapshot()
    snapshot.invalidate()
    snapshot.snapshot()

    assert len(calls) == 2
    assert snapshot.version == file_version(config_path)
    assert snapshot.stats()["misses"] == 2


def test_version_fn_overrides_paths():
    version = ["v1"]
    calls = []
    snapshot = VersionedConfigSnapshot(
        _counter_loader(calls, "x"), version_fn=lambda: tuple(version)
    )

    snapshot.snapshot()
    snapshot.snapshot()
    assert len(calls) == 1

    version[0] = "v2"
    snapshot.snapshot()
    assert len(calls) == 2


# --------------------------------------------------------------------------- #
# Staleness ceiling                                                            #
# --------------------------------------------------------------------------- #


def test_max_age_throttles_version_checks_without_rebuilding():
    """The ceiling keeps hot reads free of even the version-check cost."""
    version_checks = []
    calls = []

    def version_fn():
        version_checks.append(1)
        return "stable"

    snapshot = VersionedConfigSnapshot(
        _counter_loader(calls, "x"), version_fn=version_fn, max_age=60.0
    )

    for _ in range(100):
        assert snapshot.snapshot() == snapshot.snapshot()

    assert len(version_checks) == 1, "version check must be throttled"
    assert snapshot.stats()["version_checks"] == 1
    assert len(calls) == 1

    # Ceiling elapses but the source is unchanged: re-check, no rebuild.
    snapshot._version_checked_at -= 61.0
    snapshot.snapshot()
    assert len(version_checks) == 2
    assert len(calls) == 1, "unchanged version must not rebuild"
    assert snapshot.stats()["rebuilds"] == 1


def test_max_age_change_observed_after_ceiling():
    """A real change is picked up on the first check after the ceiling."""
    version = ["v1"]
    calls = []
    snapshot = VersionedConfigSnapshot(
        _counter_loader(calls, "x"), version_fn=lambda: tuple(version), max_age=60.0
    )

    snapshot.snapshot()
    assert len(calls) == 1

    # Still inside the ceiling: the edit is intentionally not observed yet.
    version[0] = "v2"
    snapshot.snapshot()
    assert len(calls) == 1

    # Ceiling elapses: exactly one rebuild picks up the new version.
    snapshot._version_checked_at -= 61.0
    snapshot.snapshot()
    assert len(calls) == 2
    assert snapshot.stats()["rebuilds"] == 2


def test_default_snapshot_still_checks_version_every_access():
    """Backwards compatibility: no ceiling means unchanged behaviour."""
    version_checks = []
    snapshot = VersionedConfigSnapshot(
        lambda: {"v": 1}, version_fn=lambda: version_checks.append(1) or "stable"
    )

    for _ in range(10):
        snapshot.snapshot()

    assert len(version_checks) == 10
    assert snapshot.stats()["max_age"] is None
