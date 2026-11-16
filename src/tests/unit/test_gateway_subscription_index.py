"""Indexed per-connection subscription lookups (no full-registry scans)."""

from src.api.websocket.artifacts import ArtifactSubscriptionRegistry
from src.api.websocket.sum import SumSubscriptionRegistry


class _CountingDict(dict):
    """Dict that records full-iteration calls, for scan regression tests."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.items_calls = 0

    def items(self):
        self.items_calls += 1
        return super().items()


def test_artifact_multitab_unsubscribe_uses_index():
    registry = ArtifactSubscriptionRegistry()
    registry.subscribe(7, 100, "tab-a")
    registry.subscribe(7, 100, "tab-b")
    assert registry.connection_count(7, 100) == 2

    registry.unsubscribe(7, 100, "tab-a")
    assert registry.connection_count(7, 100) == 1
    assert registry.get_subscribers(100) == {7}  # other tab keeps user live

    registry.unsubscribe(7, 100, "tab-b")
    assert registry.connection_count(7, 100) == 0
    assert registry.get_subscribers(100) == set()


def test_sum_multitab_unsubscribe_uses_index():
    registry = SumSubscriptionRegistry()
    registry.subscribe(9, 200, "tab-a")
    registry.subscribe(9, 200, "tab-b")

    registry.unsubscribe(9, 200, "tab-a")
    assert registry.connection_count(9, 200) == 1
    assert registry.get_subscribers(200) == {9}
    registry.unsubscribe_connection("tab-b", 9)
    assert registry.get_subscribers(200) == set()


def test_unsubscribe_connection_isolated_with_many_decoy_connections():
    registry = ArtifactSubscriptionRegistry()
    registry.subscribe(1, 500, "target")
    # Thousands of unrelated connections must not affect teardown of "target".
    for i in range(2000):
        registry.subscribe(100 + i, 501 + i, f"decoy-{i}")

    registry.unsubscribe_connection("target", 1)

    assert registry.get_subscribers(500) == set()
    assert registry.connection_count(1, 500) == 0
    # Decoys are untouched.
    assert registry.get_subscribers(501 + 1999) == {100 + 1999}


def test_index_calls_never_scan_all_connections():
    """Regression: unsubscribe paths must not iterate _connection_subs."""
    for registry, subscribe, resource_id in (
        (ArtifactSubscriptionRegistry(), "subscribe", 11),
        (SumSubscriptionRegistry(), "subscribe", 12),
    ):
        getattr(registry, subscribe)(3, resource_id, "tab-a")
        getattr(registry, subscribe)(3, resource_id, "tab-b")
        getattr(registry, subscribe)(4, resource_id, "other")
        # Instrument the connection map after the index has been populated.
        registry._connection_subs = _CountingDict(registry._connection_subs)
        counting = registry._connection_subs

        registry.unsubscribe(3, resource_id, "tab-a")
        registry.unsubscribe_connection("tab-b", 3)
        registry.unsubscribe_all(4)

        assert counting.items_calls == 0, "a full connection scan regressed"


def test_resubscribe_is_idempotent_in_index():
    registry = ArtifactSubscriptionRegistry()
    for _ in range(5):
        registry.subscribe(7, 42, "tab-a")
    assert registry.connection_count(7, 42) == 1
    registry.unsubscribe(7, 42, "tab-a")
    assert registry.connection_count(7, 42) == 0
