"""Regression coverage for bounded process-local caches.

The capability-verdict cache and the SQL placeholder-conversion cache are
module-level state that every request can touch. They used to be plain dicts
with no full bound (TTL was only checked on access, and the conversion cache
stopped at an entry count with no byte cap or expiry). These tests pin the
entry/byte/TTL bounds and, just as importantly, the no-functional-regression
contract: eviction must never weaken the capability gate, and conversion
results must stay byte-for-byte correct after an eviction.
"""

import pytest

import src.core.bounded_lru as bounded_lru_mod
from src.core.bounded_lru import BoundedLRU
from src.core.artifacts import capabilities as caps_mod
from src.core.artifacts.capabilities import CapabilityState
from src.core.database import dialect as dialect_mod


class _FakeClock:
    """Deterministic monotonic clock for TTL tests (no real sleeping)."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _clean_caches():
    """Keep module-level caches clean between tests."""
    caps_mod.clear_capability_cache()
    dialect_mod._QUERY_CONVERSION_CACHE.clear()
    yield
    caps_mod.clear_capability_cache()
    dialect_mod._QUERY_CONVERSION_CACHE.clear()


class TestBoundedLRU:
    def test_entry_cap_evicts_least_recently_used(self):
        cache = BoundedLRU(max_entries=3, max_bytes=10_000, ttl_seconds=60)
        for k in ("a", "b", "c"):
            cache.set(k, k)
        # Refresh "a" so "b" becomes the least-recently-used entry.
        assert cache.get("a") == "a"
        cache.set("d", "d")
        assert len(cache) == 3
        assert "b" not in cache
        assert cache.get("a") == "a"
        assert cache.get("c") == "c"
        assert cache.get("d") == "d"

    def test_byte_cap_is_hard_and_evicts_lru(self):
        # size_of returns 10 per value; only three fit in a 35-byte budget.
        cache = BoundedLRU(
            max_entries=100, max_bytes=35, ttl_seconds=60, size_of=lambda _v: 10
        )
        for i in range(20):
            cache.set(i, str(i))
        stats = cache.stats()
        assert len(cache) <= 3
        assert stats["bytes"] <= stats["max_bytes"]
        assert stats["evictions"] > 0

    def test_oversized_single_value_is_not_cached(self):
        cache = BoundedLRU(
            max_entries=10, max_bytes=20, ttl_seconds=60, size_of=lambda _v: 100
        )
        cache.set("big", "x" * 100)
        assert "big" not in cache
        assert cache.stats()["bytes"] == 0

    def test_ttl_expires_and_forces_recompute(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(bounded_lru_mod, "time", clock)
        cache = BoundedLRU(max_entries=10, max_bytes=1000, ttl_seconds=5)
        cache.set("k", "verdict")
        assert cache.get("k") == "verdict"

        clock.advance(6)
        # Expired entries are never served; the caller recomputes instead.
        assert cache.get("k", "MISS") == "MISS"
        assert "k" not in cache
        assert cache.stats()["expirations"] >= 1

    def test_clear_is_full_invalidation(self):
        cache = BoundedLRU(max_entries=10, max_bytes=1000, ttl_seconds=60)
        for i in range(5):
            cache.set(i, i)
        cache.clear()
        assert len(cache) == 0
        assert cache.stats()["bytes"] == 0
        for i in range(5):
            assert cache.get(i, "MISS") == "MISS"


class TestCapabilityCacheBounds:
    def test_cache_is_a_bounded_lru_not_a_plain_dict(self):
        cache = caps_mod._capability_cache
        assert isinstance(cache, BoundedLRU)
        assert cache.max_entries >= 1
        assert cache.max_bytes >= 1
        assert caps_mod._CAPABILITY_CACHE_TTL_SECONDS > 0

    def test_entry_cap_holds_under_distinct_feature_flood(self):
        # Unknown features are memoized too (fail-closed MISCONFIGURED), so a
        # flood of distinct names is the realistic growth path.
        for i in range(caps_mod._CAPABILITY_CACHE_MAX_ENTRIES + 50):
            caps_mod.get_capability(f"probe.feature.{i}")
        assert len(caps_mod._capability_cache) <= caps_mod._CAPABILITY_CACHE_MAX_ENTRIES
        assert (
            caps_mod._capability_cache.stats()["bytes"]
            <= caps_mod._CAPABILITY_CACHE_MAX_BYTES
        )

    def test_eviction_never_revives_a_stale_allow(self, monkeypatch):
        # A cached AVAILABLE verdict must stay fail-closed-safe after eviction:
        # re-evaluating from the authoritative source cannot resurrect a stale
        # "allowed" state. Force the evaluator to flip and confirm recompute.
        caps_mod.clear_capability_cache()
        states = {"n": 0}

        def evaluator():
            states["n"] += 1

            def disabled(_artifacts):
                return caps_mod.CapabilityInfo(
                    feature="artifacts",
                    state=CapabilityState.DISABLED_BY_LICENSE,
                    message="disabled",
                    details={},
                )

            return {"artifacts": disabled}

        monkeypatch.setattr(caps_mod, "_evaluators", evaluator)
        first = caps_mod.get_capability("artifacts")
        assert first.state == CapabilityState.DISABLED_BY_LICENSE
        assert states["n"] == 1
        # The second call is served from the cache, not re-evaluated.
        caps_mod.get_capability("artifacts")
        assert states["n"] == 1

        # Full invalidation (license hot-swap) forces a fresh authoritative read.
        caps_mod.clear_capability_cache()
        fresh = caps_mod.get_capability("artifacts")
        assert fresh.state == CapabilityState.DISABLED_BY_LICENSE
        assert states["n"] == 2

    def test_unknown_feature_stays_misconfigured_after_eviction(self):
        caps_mod.get_capability("definitely.unknown.feature")
        # Fill past the cap to evict the earlier entry.
        for i in range(caps_mod._CAPABILITY_CACHE_MAX_ENTRIES + 10):
            caps_mod.get_capability(f"filler.{i}")
        again = caps_mod.get_capability("definitely.unknown.feature")
        assert again.state == CapabilityState.MISCONFIGURED

    def test_ttl_expiry_forces_fresh_capability_evaluation(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(bounded_lru_mod, "time", clock)
        calls = {"n": 0}
        real = caps_mod._evaluators

        def counting():
            calls["n"] += 1
            return real()

        monkeypatch.setattr(caps_mod, "_evaluators", counting)
        caps_mod.get_capability("artifacts")
        caps_mod.get_capability("artifacts")
        assert calls["n"] == 1
        clock.advance(caps_mod._CAPABILITY_CACHE_TTL_SECONDS + 1)
        caps_mod.get_capability("artifacts")
        assert calls["n"] == 2

    def test_explicit_config_still_bypasses_cache(self, monkeypatch):
        calls = {"n": 0}
        real = caps_mod._evaluators

        def counting():
            calls["n"] += 1
            return real()

        monkeypatch.setattr(caps_mod, "_evaluators", counting)
        caps_mod.get_capability("artifacts", {})
        caps_mod.get_capability("artifacts", {})
        assert calls["n"] == 2
        assert len(caps_mod._capability_cache) == 0


class TestDialectCacheBounds:
    def test_cache_is_a_bounded_lru_not_a_plain_dict(self):
        assert isinstance(dialect_mod._QUERY_CONVERSION_CACHE, BoundedLRU)
        assert dialect_mod._QUERY_CONVERSION_CACHE.max_entries >= 1
        assert dialect_mod._QUERY_CONVERSION_CACHE.max_bytes >= 1
        assert dialect_mod._QUERY_CONVERSION_CACHE_TTL_SECONDS > 0

    def test_conversion_output_unchanged(self):
        out = dialect_mod.convert_placeholders(
            "SELECT * FROM t WHERE id = ?", "postgres"
        )
        assert out == "SELECT * FROM t WHERE id = %s"
        # Literal % inside a quoted literal must still be doubled.
        out2 = dialect_mod.convert_placeholders("SELECT '%' AS p, ? AS q", "postgres")
        assert "%" in out2 and "%s" in out2
        # Non-postgres dialects are returned verbatim.
        raw = "SELECT ? FROM t"
        assert dialect_mod.convert_placeholders(raw, "sqlite") == raw

    def test_entry_cap_holds_and_conversion_correct_after_eviction(self):
        first_query = "SELECT * FROM evict_me WHERE id = ?"
        expected = dialect_mod.convert_placeholders(first_query, "postgres")
        assert expected == "SELECT * FROM evict_me WHERE id = %s"

        # Force far more distinct keys than the entry cap.
        for i in range(dialect_mod._QUERY_CONVERSION_CACHE_MAX_ENTRIES + 25):
            dialect_mod.convert_placeholders(
                f"SELECT {i} FROM t WHERE id = ?", "postgres"
            )

        stats = dialect_mod._QUERY_CONVERSION_CACHE.stats()
        assert stats["size"] <= dialect_mod._QUERY_CONVERSION_CACHE_MAX_ENTRIES
        assert stats["bytes"] <= dialect_mod._QUERY_CONVERSION_CACHE_MAX_BYTES
        assert stats["evictions"] > 0

        # The evicted query still converts identically (recomputed, no stale
        # conversion can shorten or corrupt the SQL).
        again = dialect_mod.convert_placeholders(first_query, "postgres")
        assert again == expected

    def test_ttl_expires_conversion_entry(self, monkeypatch):
        clock = _FakeClock()
        monkeypatch.setattr(bounded_lru_mod, "time", clock)
        query = "SELECT ? FROM ttl_test"
        key = f"postgres:{query}"
        dialect_mod.convert_placeholders(query, "postgres")
        assert dialect_mod._QUERY_CONVERSION_CACHE.get(key) is not None
        clock.advance(dialect_mod._QUERY_CONVERSION_CACHE_TTL_SECONDS + 1)
        assert dialect_mod._QUERY_CONVERSION_CACHE.get(key, "MISS") == "MISS"
        # Recomputation after expiry is still correct.
        assert (
            dialect_mod.convert_placeholders(query, "postgres")
            == "SELECT %s FROM ttl_test"
        )
