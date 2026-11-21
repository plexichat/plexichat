"""Regression: the media-proxy rate-limit config lookup must not stat the
config file on every proxy fetch.

``_proxy_rate_limit_params()`` runs on every uncached image-proxy request.  Its
version token used to ``os.stat`` the config file on every call.  Only that
stat is now memoised behind a short staleness ceiling, while the cheap
in-memory parts of the token (loader identity + ``repr`` of the tiny
``proxy_rate_limit`` mapping) are still checked on every access.  This cuts the
stat traffic without delaying in-place config edits, which must remain visible
on the next read.  These tests pin the stat reduction, byte-identical output,
and immediate edit detection.
"""

from __future__ import annotations

from typing import Any, List

import pytest

import src.core.config.snapshot as snapshot_mod
import utils.config as config
from src.api.routes import media as media_routes


@pytest.fixture
def proxy_snapshot():
    """Isolate the proxy-policy snapshot state, counters and file-version memo."""
    snap = media_routes._proxy_policy_snapshot
    snap.invalidate()
    saved_stats = {
        key: getattr(snap, key)
        for key in ("_hits", "_misses", "_rebuilds", "_version_checks")
    }
    saved_memo = dict(media_routes._proxy_file_version_cache)
    media_routes._proxy_file_version_cache["at"] = 0.0
    media_routes._proxy_file_version_cache["version"] = None
    yield snap
    snap.invalidate()
    media_routes._proxy_file_version_cache.clear()
    media_routes._proxy_file_version_cache.update(saved_memo)
    for key, value in saved_stats.items():
        setattr(snap, key, value)


@pytest.fixture
def restore_config():
    """Restore the process-wide config globals after a test calls setup()."""
    saved_instance = config._config_instance
    saved_setup = config._setup_called
    yield
    config._config_instance = saved_instance
    config._setup_called = saved_setup


def _stat_count_during(reads: int, call, *, force_stat: bool = False) -> int:
    """Count os.stat calls issued through the snapshot module over ``reads``.

    ``force_stat`` expires the file-version memo before each read, simulating
    the old "stat on every access" behaviour for a like-for-like comparison.
    """
    real_stat = snapshot_mod.os.stat
    calls: List[Any] = []

    def counting_stat(path, *args, **kwargs):
        calls.append(path)
        return real_stat(path, *args, **kwargs)

    snapshot_mod.os.stat = counting_stat
    try:
        for _ in range(reads):
            if force_stat:
                media_routes._proxy_file_version_cache["at"] = 0.0
            call()
    finally:
        snapshot_mod.os.stat = real_stat
    return len(calls)


class TestMediaProxyPolicyStatThrottle:
    def test_ceiling_cuts_repeated_proxy_stats(self, proxy_snapshot):
        """Before: one stat per read. After: none inside the ceiling window."""
        media_routes._proxy_rate_limit_params()  # warm up the cached value

        reads = 200
        throttled = _stat_count_during(reads, media_routes._proxy_rate_limit_params)
        unthrottled = _stat_count_during(
            reads, media_routes._proxy_rate_limit_params, force_stat=True
        )

        assert unthrottled >= reads, "without the memo every proxy read stats the file"
        assert throttled == 0, "inside the ceiling no stat may be issued"
        assert throttled < unthrottled, "the memo must reduce stat traffic"

    def test_throttled_output_matches_a_forced_rebuild(self, proxy_snapshot):
        """Throttled reads must be byte-identical to a fresh build."""
        expected = media_routes._proxy_rate_limit_params()
        for _ in range(50):
            assert media_routes._proxy_rate_limit_params() == expected

        proxy_snapshot.invalidate()
        assert media_routes._proxy_rate_limit_params() == expected

    def test_in_place_rate_config_change_is_observed_immediately(
        self, proxy_snapshot, restore_config, tmp_path
    ):
        """An in-place rate-config edit is visible on the very next read."""
        path = tmp_path / "config.yaml"
        path.write_text(
            "media:\n  proxy_rate_limit:\n    requests_per_window: 120\n    window_seconds: 60\n"
        )
        config.setup(
            config_path=str(path),
            default_config={
                "media": {
                    "proxy_rate_limit": {
                        "requests_per_window": 120,
                        "window_seconds": 60,
                    }
                }
            },
        )
        proxy_snapshot.invalidate()
        assert media_routes._proxy_rate_limit_params() == (120, 60)

        # In-place edit (bypasses config.set) is folded into the version via
        # repr(rate_cfg), a cheap in-memory signal checked on every access.
        config._config_instance.config["media"]["proxy_rate_limit"] = {
            "requests_per_window": 7,
            "window_seconds": 9,
        }
        assert media_routes._proxy_rate_limit_params() == (7, 9)
