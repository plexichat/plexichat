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
