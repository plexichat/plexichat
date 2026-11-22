"""Regression: the per-request API-config read must not stat the config file
on every call.

``get_api_config()`` is called from ``SecurityHeadersMiddleware`` on every
response.  Its versioned snapshot used to check the version -- and therefore
``os.stat`` the config file -- on every access.  A short staleness ceiling now
throttles that stat while version identity (loader id + file metadata) still
governs rebuilds, so ``config.setup()`` and on-disk edits keep invalidating
the snapshot.  These tests pin the stat reduction, byte-identical output and
both invalidation triggers without relaxing any validation.
"""

from __future__ import annotations

import os
import time
from typing import Any, List

import pytest

import src.core.config.snapshot as snapshot_mod
import utils.config as config
from src.api import config as api_config


@pytest.fixture
def api_snapshot():
    """Isolate the API config snapshot's cached state and ceiling for a test."""
    snap = api_config._api_config_snapshot
    saved_max_age = snap._max_age
    snap.invalidate()
    yield snap
    snap.invalidate()
    snap._max_age = saved_max_age


@pytest.fixture
def restore_config():
    """Restore the process-wide config globals after a test calls setup()."""
    saved_instance = config._config_instance
    saved_setup = config._setup_called
    yield
    config._config_instance = saved_instance
    config._setup_called = saved_setup


def _stat_count_during(reads: int, call) -> int:
    """Count os.stat calls issued through the snapshot module over ``reads``."""
    real_stat = snapshot_mod.os.stat
    calls: List[Any] = []

    def counting_stat(path, *args, **kwargs):
        calls.append(path)
        return real_stat(path, *args, **kwargs)

    snapshot_mod.os.stat = counting_stat
    try:
        for _ in range(reads):
            call()
    finally:
        snapshot_mod.os.stat = real_stat
    return len(calls)


class TestApiConfigStatThrottle:
    def test_ceiling_cuts_repeated_config_stats_to_zero(self, api_snapshot):
        """Before: one stat per read. After: none inside the ceiling window."""
        snap = api_snapshot
        assert snap._max_age is not None and snap._max_age > 0

        reads = 200
        api_config.get_api_config()  # warm up the cached value
        throttled = _stat_count_during(reads, api_config.get_api_config)

        # Simulate the old "check version on every access" behaviour.
        snap.invalidate()
        snap._max_age = None
        api_config.get_api_config()  # warm up again for a fair comparison
        unthrottled = _stat_count_during(reads, api_config.get_api_config)

        assert unthrottled >= reads, "without a ceiling every read stats the file"
        assert throttled == 0, "inside the ceiling no stat may be issued"
        assert throttled < unthrottled, "the ceiling must reduce stat traffic"

    def test_throttled_output_matches_a_forced_rebuild(self, api_snapshot):
        settings = api_config.get_api_config()
        # Several throttled reads must all resolve the same memoized value.
        # They are equal but deliberately NOT the same object: APIConfig is
        # mutable config fed to the CORS/auth gates, so each caller gets a
        # detached copy that cannot poison the shared snapshot.
        for _ in range(50):
            assert api_config.get_api_config() == settings
            assert api_config.get_api_config() is not settings

        # Force an unconditional rebuild and confirm the *values* are identical.
        api_snapshot.invalidate()
        rebuilt = api_config.get_api_config()
        assert rebuilt is not settings
        assert rebuilt == settings

        def as_tuple(c):
            return (
                c.title,
                c.description,
                c.version,
                c.api_prefix,
                tuple(c.cors_origins),
                c.cors_allow_credentials,
                c.max_request_body_size,
            )

        assert as_tuple(rebuilt) == as_tuple(settings)

    def test_new_config_setup_invalidates_after_ceiling(
        self, api_snapshot, restore_config, tmp_path
    ):
        """``config.setup()`` builds a new loader -> new version -> rebuild."""
        snap = api_snapshot
        first = tmp_path / "one.yaml"
        first.write_text("api:\n  title: One\n")
        config.setup(config_path=str(first), default_config={"api": {"title": "One"}})
        snap.invalidate()
        assert api_config.get_api_config().title == "One"
        old_loader = config._config_instance  # keep alive so its id can't be reused
        old_version = snap.version

        second = tmp_path / "two.yaml"
        second.write_text("api:\n  title: Two\n")
        config.setup(config_path=str(second), default_config={"api": {"title": "Two"}})

        # Inside the ceiling the cached value is still served (that is the
        # point of the throttle). Once the ceiling elapses, the new loader id
        # moves the version and the next access rebuilds from the new source.
        assert api_config.get_api_config().title == "One"
        snap._version_checked_at = time.monotonic() - (snap._max_age + 1)
        result = api_config.get_api_config()
        assert result.title == "Two"
        assert snap.version != old_version
        del old_loader

    def test_file_change_rebuilds_only_after_ceiling(
        self, api_snapshot, restore_config, tmp_path
    ):
        """An on-disk edit moves the version but is throttled by the ceiling."""
        snap = api_snapshot
        path = tmp_path / "config.yaml"
        path.write_text("api:\n  title: V1\n")
        config.setup(config_path=str(path), default_config={"api": {"title": "V1"}})
        snap.invalidate()

        assert api_config.get_api_config().title == "V1"
        version_before = snap.version
        rebuilds_before = snap.stats()["rebuilds"]

        path.write_text("api:\n  title: V2\n")
        future = time.time() + 10
        os.utime(path, (future, future))

        # Still inside the ceiling: no stat, no rebuild, same value.
        assert api_config.get_api_config().title == "V1"
        assert snap.stats()["rebuilds"] == rebuilds_before

        # Once the ceiling elapses the new mtime/size is observed and rebuilds.
        snap._version_checked_at = time.monotonic() - (snap._max_age + 1)
        api_config.get_api_config()
        assert snap.version != version_before
        assert snap.stats()["rebuilds"] == rebuilds_before + 1

    def test_clear_cache_forces_immediate_reload(
        self, api_snapshot, restore_config, tmp_path
    ):
        """Explicit invalidation bypasses the ceiling (admin reload path)."""
        snap = api_snapshot
        path = tmp_path / "config.yaml"
        path.write_text("api:\n  title: A\n")
        config.setup(config_path=str(path), default_config={"api": {"title": "A"}})
        snap.invalidate()
        assert api_config.get_api_config().title == "A"

        path.write_text("api:\n  title: B\n")
        future = time.time() + 10
        os.utime(path, (future, future))
        config.setup(config_path=str(path), default_config={"api": {"title": "B"}})
        api_config.clear_api_config_cache()
        assert api_config.get_api_config().title == "B"

    def test_validation_still_fails_closed_on_wildcard_cors(
        self, api_snapshot, restore_config, tmp_path
    ):
        """The ceiling changes only caching -- no security validation is weakened."""
        snap = api_snapshot
        path = tmp_path / "config.yaml"
        path.write_text("api:\n  cors_origins: ['*']\n  allow_wildcard_cors: false\n")
        config.setup(
            config_path=str(path),
            default_config={
                "api": {"cors_origins": ["*"], "allow_wildcard_cors": False}
            },
        )
        snap.invalidate()
        assert api_config.get_api_config().cors_origins == []
