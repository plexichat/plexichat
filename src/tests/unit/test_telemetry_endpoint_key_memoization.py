"""Regression: telemetry endpoint-key computation is bounded and memoized.

``submit_response_times`` is an unauthenticated sink. For every submitted
entry it normalizes the endpoint (a regex pass per path segment) and checks it
against the registered route templates (a scan of every template). With a few
hundred routes that is ~70us of pure CPU per entry, paid again and again for the
same handful of endpoints. The pure computation is now memoized in two bounded
LRUs; these tests pin that:

* repeated endpoints recompute only once (the actual perf fix);
* a verdict can never outlive a route-template reload (fail closed);
* both tiers stay under hard entry caps under a key flood; and
* caching never changes the normalization/allowlist result (parity).
"""

from __future__ import annotations

import pytest

from src.core import telemetry


@pytest.fixture(autouse=True)
def _clean_caches():
    telemetry.clear_endpoint_caches()
    yield
    telemetry.clear_endpoint_caches()


def test_repeated_endpoint_key_computation_is_memoized(monkeypatch):
    telemetry.register_route_templates(
        ["/api/v1/users/{user_id}/messages", "/api/v1/servers/{server_id}"]
    )
    telemetry.clear_endpoint_caches()

    calls = {"normalize": 0, "match": 0}
    real_normalize = telemetry._normalize_endpoint_uncached
    real_match = telemetry._matches_registered_template_uncached

    def counting_normalize(endpoint):
        calls["normalize"] += 1
        return real_normalize(endpoint)

    def counting_match(endpoint):
        calls["match"] += 1
        return real_match(endpoint)

    monkeypatch.setattr(telemetry, "_normalize_endpoint_uncached", counting_normalize)
    monkeypatch.setattr(
        telemetry, "_matches_registered_template_uncached", counting_match
    )

    raw = "/api/v1/users/550e8400-e29b-41d4-a716-446655440000/messages"
    before = telemetry.get_endpoint_cache_stats()
    for _ in range(50):
        normalized = telemetry._normalize_endpoint(raw)
        assert telemetry._matches_registered_template(normalized)

    # UUID segments normalize to the generic {id} placeholder.
    assert normalized == "/api/v1/users/{id}/messages"
    # The expensive pure work ran once, not once per submission.
    assert calls == {"normalize": 1, "match": 1}

    # The cache counters are process-wide observability, so assert this test's
    # own contribution: a warm cache elsewhere in the worker must not skew it.
    after = telemetry.get_endpoint_cache_stats()
    assert after["normalized"]["hits"] - before["normalized"]["hits"] == 49
    assert after["normalized"]["misses"] - before["normalized"]["misses"] == 1
    assert after["template"]["hits"] - before["template"]["hits"] == 49
    assert after["template"]["misses"] - before["template"]["misses"] == 1


def test_template_verdict_cannot_survive_route_reload():
    telemetry.register_route_templates(["/api/v1/users/{user_id}/messages"])
    endpoint = "/api/v1/users/{user_id}/messages"
    assert telemetry._matches_registered_template(endpoint) is True

    # A reload replaces the source of truth. A previously accepted endpoint
    # that is no longer a registered template must be rejected (fail closed),
    # never served from a warm entry.
    telemetry.register_route_templates(["/api/v1/servers/{server_id}"])
    assert telemetry._matches_registered_template(endpoint) is False
    assert telemetry._matches_registered_template("/api/v1/servers/{server_id}") is True

    stats = telemetry.get_endpoint_cache_stats()
    # The pre-reload verdict was dropped; only the two post-reload lookups
    # (rejected old endpoint, accepted new one) are present.
    assert stats["template"]["size"] == 2


def test_caches_are_bounded_under_key_flood():
    telemetry.clear_endpoint_caches()
    limit = telemetry._ENDPOINT_CACHE_MAX_ENTRIES
    for i in range(limit + 200):
        telemetry._normalize_endpoint(f"/api/v1/resource{i}")
        telemetry._matches_registered_template(f"/api/v1/resource{i}")

    stats = telemetry.get_endpoint_cache_stats()
    assert stats["normalized"]["size"] <= limit
    assert stats["template"]["size"] <= limit
    assert stats["normalized"]["evictions"] > 0
    assert stats["template"]["evictions"] > 0


@pytest.mark.parametrize(
    "raw",
    [
        "/api/v1/users/123456/messages",
        "/api/v1/channels/550e8400-e29b-41d4-a716-446655440000",
        "/api/v1/webhooks/abcdefghijklmnopqrst/abc",
        "/api/v1/unknown/123456",
        "/",
        "",
    ],
)
def test_cache_preserves_exact_computation(raw):
    expected = telemetry._normalize_endpoint_uncached(raw)
    # First call is a miss, the second a hit; both must agree with the pure
    # function and with each other (no key aliasing between different inputs).
    first = telemetry._normalize_endpoint(raw)
    second = telemetry._normalize_endpoint(raw)
    assert first == second == expected


def test_distinct_raw_endpoints_do_not_share_entries():
    telemetry.clear_endpoint_caches()
    before = telemetry.get_endpoint_cache_stats()["normalized"]["misses"]
    a = telemetry._normalize_endpoint("/api/v1/users/111111/messages")
    b = telemetry._normalize_endpoint("/api/v1/users/222222/messages")
    # Both normalize to the same metric key (that is the point), yet they are
    # separate cache keys: a distinct raw input can never read another's slot.
    assert a == b == "/api/v1/users/{user_id}/messages"
    after = telemetry.get_endpoint_cache_stats()["normalized"]["misses"]
    assert after - before == 2
