"""Regression: the per-request derived-policy read must not stat the config
file on every call.

``get_static_policy()`` is the first call the authentication middleware makes on
every HTTP request. Its versioned snapshot used to check the version -- and
therefore ``os.stat`` the config file -- on every access. A short staleness
ceiling now throttles that stat while version identity (loader id, file
mtime/size, API-config identity) still governs rebuilds, so a new config loader
or an on-disk edit is observed on the next check and
``clear_static_policy_cache()`` stays immediate. These tests pin the stat
reduction, byte-identical output and both invalidation triggers without
relaxing any auth gate.
"""

from __future__ import annotations

import time
from typing import Any, List

import pytest

import src.core.config.snapshot as snapshot_mod
import utils.config as config
from src.api import config as api_config
from src.api.middleware import policy_cache


@pytest.fixture
def policy_snapshot():
    """Isolate the policy snapshot's cached state and ceiling for a test."""
    snap = policy_cache._policy_snapshot
    saved_max_age = snap._max_age
    snap.invalidate()
    api_config.clear_api_config_cache()
    yield snap
    snap.invalidate()
    api_config.clear_api_config_cache()
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


class TestPolicyCacheStatThrottle:
    def test_ceiling_cuts_repeated_policy_stats_to_zero(
        self, policy_snapshot, restore_config, tmp_path
    ):
        """Before: one stat per read. After: none inside the ceiling window."""
        snap = policy_snapshot
        assert snap._max_age is not None and snap._max_age > 0

        path = tmp_path / "config.yaml"
        path.write_text("api:\n  api_prefix: /api/v1\n")
        config.setup(
            config_path=str(path), default_config={"api": {"api_prefix": "/api/v1"}}
        )
        snap.invalidate()

        reads = 200
        policy_cache.get_static_policy()  # warm up the cached value
        throttled = _stat_count_during(reads, policy_cache.get_static_policy)

        # Simulate the old "check version on every access" behaviour.
        snap.invalidate()
        snap._max_age = None
        policy_cache.get_static_policy()  # warm up again for a fair comparison
        unthrottled = _stat_count_during(reads, policy_cache.get_static_policy)

        assert unthrottled >= reads, "without a ceiling every read stats the file"
        assert throttled == 0, "inside the ceiling no stat may be issued"
        assert throttled < unthrottled, "the ceiling must reduce stat traffic"

    def test_throttled_policy_matches_a_forced_rebuild(self, policy_snapshot):
        first = policy_cache.get_static_policy()
        # Several throttled reads must all return the identical object.
        for _ in range(50):
            assert policy_cache.get_static_policy() is first

        # Force an unconditional rebuild and confirm the *values* are identical.
        policy_snapshot.invalidate()
        rebuilt = policy_cache.get_static_policy()
        assert rebuilt is not first
        assert rebuilt == first

    def test_prefix_change_observed_after_ceiling(
        self, policy_snapshot, restore_config, tmp_path
    ):
        """A new config loader must reach the auth gate within the ceiling."""
        snap = policy_snapshot
        first = tmp_path / "one.yaml"
        first.write_text("api:\n  api_prefix: /api/v1\n")
        config.setup(
            config_path=str(first), default_config={"api": {"api_prefix": "/api/v1"}}
        )
        snap.invalidate()
        assert policy_cache.get_static_policy().api_prefix == "/api/v1"

        second = tmp_path / "two.yaml"
        second.write_text("api:\n  api_prefix: /api/v2\n")
        config.setup(
            config_path=str(second), default_config={"api": {"api_prefix": "/api/v2"}}
        )
        old_loader = config._config_instance  # keep alive so its id cannot be reused

        # Inside the ceiling the cached policy (and its auth gate) is served.
        assert policy_cache.get_static_policy().api_prefix == "/api/v1"

        # Once both ceilings lapse the new loader id moves the version and the
        # next access rebuilds the policy from the new config.
        api_config.clear_api_config_cache()
        snap._version_checked_at = time.monotonic() - (snap._max_age + 1)
        assert policy_cache.get_static_policy().api_prefix == "/api/v2"
        del old_loader

    def test_clear_policy_cache_forces_immediate_reload(
        self, policy_snapshot, restore_config, tmp_path
    ):
        """Explicit invalidation bypasses the ceiling (admin reload path)."""
        snap = policy_snapshot
        first = tmp_path / "one.yaml"
        first.write_text("api:\n  api_prefix: /a\n")
        config.setup(
            config_path=str(first), default_config={"api": {"api_prefix": "/a"}}
        )
        snap.invalidate()
        assert policy_cache.get_static_policy().api_prefix == "/a"

        second = tmp_path / "two.yaml"
        second.write_text("api:\n  api_prefix: /b\n")
        config.setup(
            config_path=str(second), default_config={"api": {"api_prefix": "/b"}}
        )
        api_config.clear_api_config_cache()
        policy_cache.clear_static_policy_cache()
        assert policy_cache.get_static_policy().api_prefix == "/b"
