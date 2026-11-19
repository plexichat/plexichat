"""The rate-limit route keyer resolves the API prefix once per config version.

``extract_route_info`` runs on every rate-limited request (frequently twice:
once in the auth middleware and once in the rate-limit middleware). It used to
call ``config.get("api", {}).get("api_prefix", ...)`` and rebuild the normalized
string on each of those calls, even though the prefix is static until config
changes. It now reads a versioned snapshot keyed on the config loader identity
plus the config file's mtime/size, with a short staleness ceiling so the hot
path skips even the version-check stat between checks.

These tests pin: identical output, a single compute and a single version-check
stat across many requests, immediate invalidation, invalidation on config
setup / on-disk edits, and no weakening of the prefix normalization or the
route-keying behaviour that depends on it.
"""

import os
import time

import pytest

import utils.config as config
import src.core.config.snapshot as snapshot_mod
from src.core.ratelimit import middleware


# Mutable holders monkeypatched into ``config.get`` by the in-memory tests.
_CURRENT = {"prefix": "/api/v1"}
_PREFIX = {"value": "/api/v1"}


class _Loader:
    """Minimal stand-in for the live ConfigLoader used by version tokens."""

    def __init__(self, path, values=None):
        self.config_path = str(path)
        self.config = values if values is not None else {}

    def get(self, key, default=None):
        current = self.config
        for part in key.split("."):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def _invalidate_cache(self):
        pass


@pytest.fixture(autouse=True)
def _fresh_prefix_cache():
    """Each test starts and ends with an empty prefix snapshot."""
    middleware.invalidate_api_prefix_cache()
    yield
    middleware.invalidate_api_prefix_cache()


def _count_loader(snapshot, calls, monkeypatch):
    """Wrap the snapshot loader with a call counter (restored after the test)."""
    real_loader = snapshot._loader

    def counting():
        calls.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting)


def test_identical_output_single_compute_and_single_version_check(monkeypatch):
    """One compute and one version check serve every hot-path request.

    ``config_source_version`` stats the config file on each version check; with
    the staleness ceiling that stat (and the config read it implies) happens
    once for the whole burst instead of once per request.
    """
    snapshot = middleware._api_prefix_snapshot
    loads, versions = [], []
    real_version_fn = snapshot._version_fn

    def counting_version():
        versions.append(1)
        return real_version_fn()

    _count_loader(snapshot, loads, monkeypatch)
    monkeypatch.setattr(snapshot, "_version_fn", counting_version)
    start_rebuilds = snapshot.stats()["rebuilds"]

    results = [
        middleware.extract_route_info("/api/v1/users/@me", "GET") for _ in range(200)
    ]

    assert all(result == results[0] for result in results)
    assert len(loads) == 1, "the static prefix must be computed once"
    assert len(versions) == 1, "the version-check stat must be throttled"
    assert snapshot.stats()["rebuilds"] - start_rebuilds == 1


def test_config_file_stat_count_bounded_by_staleness_ceiling(tmp_path, monkeypatch):
    """The config file is stat-ed once per ceiling window, not per request."""
    path = tmp_path / "config.yaml"
    path.write_text("api:\n  api_prefix: /api/v1\n")
    monkeypatch.setattr(config, "_config_instance", _Loader(path))

    real_stat = snapshot_mod.os.stat
    stats = []

    def counting_stat(stat_path, *args, **kwargs):
        if str(stat_path) == str(path):
            stats.append(1)
        return real_stat(stat_path, *args, **kwargs)

    monkeypatch.setattr(snapshot_mod.os, "stat", counting_stat)
    middleware.invalidate_api_prefix_cache()

    for _ in range(200):
        assert middleware._resolve_api_prefix() == "/api/v1"

    assert len(stats) == 1, "one stat per staleness window, not one per call"


def test_explicit_invalidation_forces_exactly_one_rebuild(monkeypatch):
    """A reload/admin path can drop the snapshot for immediate refresh."""
    snapshot = middleware._api_prefix_snapshot
    loads = []
    _count_loader(snapshot, loads, monkeypatch)
    start_rebuilds = snapshot.stats()["rebuilds"]

    before = middleware._resolve_api_prefix()
    middleware.invalidate_api_prefix_cache()
    after = middleware._resolve_api_prefix()

    assert after == before
    assert len(loads) == 2
    assert snapshot.stats()["rebuilds"] - start_rebuilds == 2


def test_in_memory_config_change_rebuilds_prefix(monkeypatch):
    """A changed config value rebuilds on the next version check."""
    snapshot = middleware._api_prefix_snapshot
    loads = []
    _count_loader(snapshot, loads, monkeypatch)

    real_get = config.get

    def get_with_override(key, default=None):
        if key == "api":
            return {"api_prefix": _CURRENT["prefix"]}
        return real_get(key, default)

    _CURRENT["prefix"] = "/api/v1"
    monkeypatch.setattr(config, "get", get_with_override)
    monkeypatch.setattr(snapshot, "_max_age", None)  # check every access

    assert middleware._resolve_api_prefix() == "/api/v1"
    _CURRENT["prefix"] = "/api/v2/"
    assert middleware._resolve_api_prefix() == "/api/v2"
    assert len(loads) == 2, "a config change must cause exactly one rebuild"


def test_on_disk_mtime_change_moves_config_version(tmp_path, monkeypatch):
    """Both on-disk edits and a new config.setup() move the version token."""
    path = tmp_path / "config.yaml"
    path.write_text("api:\n  api_prefix: /api/v1\n")
    monkeypatch.setattr(config, "_config_instance", _Loader(path))

    original = middleware._api_prefix_version()

    future = time.time() + 5
    os.utime(path, (future, future))
    assert middleware._api_prefix_version() != original, (
        "mtime change must move version"
    )

    monkeypatch.setattr(config, "_config_instance", _Loader(path))
    assert middleware._api_prefix_version() != original, "new setup() must move version"


def test_prefix_normalization_and_route_keying_are_unchanged(monkeypatch):
    """Caching must not weaken trailing-slash, fallback or route-key behaviour."""
    snapshot = middleware._api_prefix_snapshot
    monkeypatch.setattr(snapshot, "_max_age", None)

    real_get = config.get

    def get_with_override(key, default=None):
        if key == "api":
            return {"api_prefix": _PREFIX["value"]}
        return real_get(key, default)

    _PREFIX["value"] = "/api/v2/"
    monkeypatch.setattr(config, "get", get_with_override)
    assert middleware._resolve_api_prefix() == "/api/v2"
    # A non-default prefix is rewritten to the canonical prefix the strict
    # per-route limit tables are keyed on.
    assert (
        middleware.extract_route_info("/api/v2/users/123", "GET")[0]
        == "GET /users/{id}"
    )

    # Empty prefix falls back to the default, as before.
    _PREFIX["value"] = ""
    assert middleware._resolve_api_prefix() == "/api/v1"

    # A broken config read still fails closed to the default.
    def broken_get(key, default=None):
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(config, "get", broken_get)
    assert middleware._resolve_api_prefix() == "/api/v1"
