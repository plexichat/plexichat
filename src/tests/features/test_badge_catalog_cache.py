"""The static badge catalogue is computed once and served from a versioned
snapshot on the profile/message hot path.

``Badge.get_badge_info`` used to rebuild the entire catalogue (eight ``Badge``
objects) on every call just to look up one entry.  It now reads a versioned
snapshot keyed on the config loader identity + config file mtime/size, with a
short staleness ceiling so the hot path does not even stat the config file on
every request.  Boot warm-up primes that same snapshot off the request path so
the first real profile/message request is a hit rather than the cold build.

These tests pin: identical output, a single compute across many calls,
invalidation (explicit and config-driven) forcing exactly one rebuild, no
weakening of the badge fallback/validation behaviour, and the warm-up contract
(primes once, idempotent, non-fatal, and invoked by ``warm_once``).
"""

import os
import time

import pytest

import utils.config as config
from src.core.config import config_source_version
from src.core.features import models


@pytest.fixture(autouse=True)
def _fresh_badge_catalog():
    """Every test starts and ends with an empty catalogue snapshot."""
    models.invalidate_badge_catalog_cache()
    yield
    models.invalidate_badge_catalog_cache()


def test_identical_output_and_single_compute(monkeypatch):
    """One catalogue build serves every hot-path lookup, with stable output."""
    snapshot = models._badge_catalog_snapshot
    real_loader = snapshot._loader
    builds = []

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    # The snapshot's counters are cumulative for the process: an earlier test
    # in the same shard may already have built the catalogue once, so measure
    # this test's build as a delta instead of an absolute count.
    baseline = snapshot.stats()["rebuilds"]
    results = [models.Badge.get_badge_info("staff") for _ in range(200)]

    assert all(result == results[0] for result in results)
    assert len(builds) == 1, "static catalogue must be computed once"
    assert snapshot.stats()["rebuilds"] == baseline + 1


def test_returned_badge_is_a_copy_not_the_cached_template():
    """Callers mutating a result must not poison the cached catalogue."""
    first = models.Badge.get_badge_info("staff")
    first.display_name = "TAMPERED"

    second = models.Badge.get_badge_info("staff")

    assert second.display_name == "Staff"
    assert first is not second


def test_explicit_invalidation_forces_one_rebuild(monkeypatch):
    """An admin/config reload path can drop the snapshot immediately."""
    snapshot = models._badge_catalog_snapshot
    real_loader = snapshot._loader
    builds = []

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    before = models.Badge.get_badge_info("staff")
    models.invalidate_badge_catalog_cache()
    after = models.Badge.get_badge_info("staff")

    assert after == before
    assert len(builds) == 2


def test_config_source_version_tracks_loader_and_file(tmp_path, monkeypatch):
    """Config setup (new loader) and on-disk edits both move the version."""

    class _Loader:
        def __init__(self, path):
            self.config_path = str(path)

    path = tmp_path / "config.yaml"
    path.write_text("a: 1\n")
    monkeypatch.setattr(config, "_config_instance", _Loader(path))
    original = config_source_version()

    future = time.time() + 5
    os.utime(path, (future, future))
    assert config_source_version() != original, "mtime change must move version"

    monkeypatch.setattr(config, "_config_instance", _Loader(path))
    assert config_source_version() != original, "new config.setup() must move version"


def test_config_change_rebuilds_catalogue(monkeypatch):
    """A changed version token is observed by the snapshot on its next check."""
    snapshot = models._badge_catalog_snapshot
    real_loader = snapshot._loader
    builds = []

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    before = models.Badge.get_badge_info("staff")
    assert len(builds) == 1

    # Simulate the ceiling elapsing, then an on-disk config edit.
    version = ["v1"]

    def changing_version():
        return tuple(version)

    monkeypatch.setattr(snapshot, "_version_fn", changing_version)
    snapshot._version_checked_at = None
    version[0] = "v2"

    after = models.Badge.get_badge_info("staff")

    assert after == before
    assert len(builds) == 2, "a config change must cause exactly one rebuild"


def test_unknown_badge_fallback_still_applies():
    """Caching must not change or bypass the badge-name grammar/fallback."""
    info = models.Badge.get_badge_info("totally_unknown_badge")

    assert info.name == "totally_unknown_badge"
    assert info.display_name == "Totally Unknown Badge"
    assert info.description == ""
    assert info.icon == "🏷️"


def test_all_known_badges_resolve():
    """Every declared badge still maps to its full display information."""
    from src.core.features.models import AVAILABLE_BADGES

    for name in AVAILABLE_BADGES:
        info = models.Badge.get_badge_info(name)
        assert info.name == name
        assert info.display_name
        assert info.icon


# --------------------------------------------------------------------------- #
# Boot warm-up                                                                 #
# --------------------------------------------------------------------------- #


def test_warmup_primes_catalogue_before_first_request(monkeypatch):
    """Warming builds the catalogue once; the hot path then only reads it."""
    snapshot = models._badge_catalog_snapshot
    real_loader = snapshot._loader
    builds = []

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    baseline = snapshot.stats()["rebuilds"]
    models.warm_badge_catalog_cache()
    assert len(builds) == 1, "warm-up must prime the catalogue exactly once"
    assert snapshot.stats()["rebuilds"] == baseline + 1

    results = [models.Badge.get_badge_info("staff") for _ in range(200)]
    assert all(result == results[0] for result in results)
    assert len(builds) == 1, "warmed catalog serves every request without rebuild"


def test_warmup_is_idempotent_within_ceiling(monkeypatch):
    """Repeated warm-up calls must not multiply builds."""
    snapshot = models._badge_catalog_snapshot
    real_loader = snapshot._loader
    builds = []

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    models.warm_badge_catalog_cache()
    models.warm_badge_catalog_cache()
    models.warm_badge_catalog_cache()

    assert len(builds) == 1


def test_warmup_does_not_bypass_config_invalidation(monkeypatch):
    """A warmed catalogue is still rebuilt once after an explicit invalidate."""
    snapshot = models._badge_catalog_snapshot
    real_loader = snapshot._loader
    builds = []

    def counting_loader():
        builds.append(1)
        return real_loader()

    monkeypatch.setattr(snapshot, "_loader", counting_loader)

    models.warm_badge_catalog_cache()
    before = models.Badge.get_badge_info("staff")
    models.invalidate_badge_catalog_cache()
    after = models.Badge.get_badge_info("staff")

    assert after == before
    assert len(builds) == 2, "invalidation must force exactly one fresh build"


def test_warmup_is_non_fatal(monkeypatch):
    """A broken loader is logged and swallowed, never raised into startup."""
    snapshot = models._badge_catalog_snapshot

    def boom():
        raise RuntimeError("catalogue exploded")

    monkeypatch.setattr(snapshot, "_loader", boom)

    # Must not raise even though the underlying build fails.
    models.warm_badge_catalog_cache()


def test_warm_once_invokes_badge_warmup(monkeypatch):
    """The boot warm-up task actually primes the badge catalogue."""
    from src.api import warmup

    called = []
    monkeypatch.setattr(warmup, "_warm_badge_catalog", lambda: called.append(True))

    warmup.warm_once()

    assert called == [True]
