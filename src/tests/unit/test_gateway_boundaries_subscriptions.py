"""Per-connection subscription caps for artifact and workbook fabrics."""

from src.api.websocket.artifacts import ArtifactSubscriptionRegistry
from src.api.websocket.boundaries import MAX_SUBSCRIPTIONS_PER_CONNECTION
from src.api.websocket.sum import SumSubscriptionRegistry


def test_artifact_registry_cap():
    registry = ArtifactSubscriptionRegistry()
    for artifact_id in range(1, MAX_SUBSCRIPTIONS_PER_CONNECTION + 1):
        assert registry.subscribe(7, artifact_id, "conn-1") is True
    assert (
        registry.subscribe(7, MAX_SUBSCRIPTIONS_PER_CONNECTION + 1, "conn-1") is False
    )
    assert registry.get_subscribers(MAX_SUBSCRIPTIONS_PER_CONNECTION + 1) == set()


def test_artifact_resubscribe_is_idempotent():
    registry = ArtifactSubscriptionRegistry()
    for artifact_id in range(1, MAX_SUBSCRIPTIONS_PER_CONNECTION + 1):
        registry.subscribe(7, artifact_id, "conn-1")
    # Re-subscribing to an already-held id must stay allowed at the cap.
    assert registry.subscribe(7, 1, "conn-1") is True


def test_artifact_cap_is_per_connection():
    registry = ArtifactSubscriptionRegistry()
    for artifact_id in range(1, MAX_SUBSCRIPTIONS_PER_CONNECTION + 1):
        registry.subscribe(7, artifact_id, "conn-1")
    # A different connection has its own budget.
    assert registry.subscribe(7, 9999, "conn-2") is True


def test_sum_registry_cap():
    registry = SumSubscriptionRegistry()
    for workbook_id in range(1, MAX_SUBSCRIPTIONS_PER_CONNECTION + 1):
        assert registry.subscribe(7, workbook_id, "conn-1") is True
    assert (
        registry.subscribe(7, MAX_SUBSCRIPTIONS_PER_CONNECTION + 1, "conn-1") is False
    )
    assert registry.get_subscribers(MAX_SUBSCRIPTIONS_PER_CONNECTION + 1) == set()
