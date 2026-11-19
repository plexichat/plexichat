"""Regression tests for cross-worker Valkey pub/sub scaling hardening.

The ``PubSubMixin`` used to keep no local record of its subscriptions:
``subscribe`` forwarded an arbitrary channel set to GLIDE with no ceiling,
and a reconnected client silently lost every subscription. These tests pin
the two fixes: a hard-bounded, deduped subscription registry and restoration
of those subscriptions after a reconnect.
"""

import pytest

from src.core.database.valkey_client.pubsub import PubSubMixin


class _FakeSyncClient:
    """Minimal GLIDE-shaped client that records channel membership."""

    def __init__(self):
        self.subscriptions = set()
        self.published = []

    def subscribe(self, channels, timeout_ms=0):
        self.subscriptions.update(channels)

    def unsubscribe(self, channels=None, timeout_ms=0):
        if channels is None:
            self.subscriptions.clear()
        else:
            self.subscriptions -= set(channels)

    def publish(self, message, channel):
        self.published.append((channel, message))
        return 0

    def try_get_pubsub_message(self):
        return None

    def get_pubsub_message(self):
        return None


class _FakeAsyncClient(_FakeSyncClient):
    """Awaitable mirror for the ``a*`` methods."""

    async def subscribe(self, channels, timeout_ms=0):
        return super().subscribe(channels, timeout_ms)

    async def unsubscribe(self, channels=None, timeout_ms=0):
        return super().unsubscribe(channels, timeout_ms)

    async def publish(self, message, channel):
        return super().publish(message, channel)

    async def get_pubsub_message(self):
        return None


class _Harness(PubSubMixin):
    """PubSubMixin standalone, without pulling in config / real GLIDE."""

    def __init__(self, sync_client=None, async_client=None):
        self.enabled = True
        self._client = sync_client
        self._connected = sync_client is not None
        self._aclient = async_client
        self._aconnected = async_client is not None
        self._pubsub_handle = object()
        self.key_prefix = "test:"
        self._listening = False
        # No super().__init__(): the mixin's state is created lazily.


def _prefix(name):
    return f"test:{name}"


# --- bounded subscription set -------------------------------------------


def test_subscription_set_is_hard_bounded():
    client = _FakeSyncClient()
    pubsub = _Harness(sync_client=client)
    pubsub._MAX_SUBSCRIPTIONS = 3

    pubsub.subscribe("a", "b", "c", "d", "e")

    metrics = pubsub.get_subscription_metrics()
    assert metrics["subscriptions"] == 3
    assert metrics["max"] == 3
    assert metrics["drops"] == 2
    assert metrics["listening"] is True
    # The client itself never saw the refused channels.
    assert client.subscriptions == {_prefix("a"), _prefix("b"), _prefix("c")}


def test_subscribe_is_deduped_against_registry():
    client = _FakeSyncClient()
    pubsub = _Harness(sync_client=client)
    pubsub._MAX_SUBSCRIPTIONS = 3

    pubsub.subscribe("a", "b", "c")
    before = dict(pubsub.get_subscription_metrics())
    # Re-subscribing to an already-tracked channel is a no-op (dedupe).
    pubsub.subscribe("b")
    after = pubsub.get_subscription_metrics()
    assert after["subscriptions"] == before["subscriptions"] == 3
    assert after["drops"] == before["drops"] == 0
    assert client.subscriptions == {_prefix("a"), _prefix("b"), _prefix("c")}


def test_full_registry_refuses_new_subscriptions():
    client = _FakeSyncClient()
    pubsub = _Harness(sync_client=client)
    pubsub._MAX_SUBSCRIPTIONS = 2

    pubsub.subscribe("a", "b")
    pubsub.subscribe("c")

    assert pubsub.get_subscription_metrics()["subscriptions"] == 2
    assert pubsub.get_subscription_metrics()["drops"] == 1
    assert _prefix("c") not in client.subscriptions


def test_unsubscribe_prunes_registry_and_allows_reuse():
    client = _FakeSyncClient()
    pubsub = _Harness(sync_client=client)
    pubsub._MAX_SUBSCRIPTIONS = 2

    pubsub.subscribe("a", "b")
    pubsub.unsubscribe("a")
    assert pubsub.get_subscription_metrics()["subscriptions"] == 1
    assert _prefix("a") not in client.subscriptions

    # A freed slot can be reused without hitting the cap.
    pubsub.subscribe("c")
    metrics = pubsub.get_subscription_metrics()
    assert metrics["subscriptions"] == 2
    assert metrics["drops"] == 0
    assert client.subscriptions == {_prefix("b"), _prefix("c")}


def test_unsubscribe_all_clears_registry():
    client = _FakeSyncClient()
    pubsub = _Harness(sync_client=client)
    pubsub.subscribe("a", "b")

    pubsub.unsubscribe()

    metrics = pubsub.get_subscription_metrics()
    assert metrics["subscriptions"] == 0
    assert metrics["listening"] is False
    assert client.subscriptions == set()


# --- restoration after reconnect ----------------------------------------


def test_resubscribe_restores_subscriptions_after_reconnect():
    first = _FakeSyncClient()
    pubsub = _Harness(sync_client=first)
    pubsub.subscribe("a", "b", "c")

    # Simulate a dropped connection: base.connect() would create a new client,
    # which knows nothing about the old subscriptions.
    replacement = _FakeSyncClient()
    pubsub._client = replacement
    assert replacement.subscriptions == set()

    restored = pubsub.resubscribe()

    assert restored == 3
    assert replacement.subscriptions == {_prefix("a"), _prefix("b"), _prefix("c")}
    assert pubsub.get_subscription_metrics()["listening"] is True


def test_resubscribe_with_no_tracked_channels_is_noop():
    client = _FakeSyncClient()
    pubsub = _Harness(sync_client=client)
    assert pubsub.resubscribe() == 0
    assert client.subscriptions == set()


def test_connect_hook_resubscribes_after_reconnect(monkeypatch):
    from src.core.database.valkey_client.base import ValkeyClientBase

    first = _FakeSyncClient()
    pubsub = _Harness(sync_client=first)
    pubsub.subscribe("a", "b")

    replacement = _FakeSyncClient()

    def _fake_connect(self):
        self._client = replacement
        self._connected = True
        return True

    monkeypatch.setattr(ValkeyClientBase, "connect", _fake_connect)

    assert pubsub.connect() is True
    # The connect hook re-applied the tracked subscriptions to the new handle.
    assert replacement.subscriptions == {_prefix("a"), _prefix("b")}


# --- async surface -------------------------------------------------------


@pytest.mark.asyncio
async def test_async_subscribe_bounded_and_resubscribe():
    async_client = _FakeAsyncClient()
    pubsub = _Harness(async_client=async_client)
    pubsub._MAX_SUBSCRIPTIONS = 2

    await pubsub.asubscribe("a", "b", "c")
    metrics = pubsub.get_subscription_metrics()
    assert metrics["subscriptions"] == 2
    assert metrics["drops"] == 1
    assert async_client.subscriptions == {_prefix("a"), _prefix("b")}

    # Reconnect: swap the async handle, then restore.
    replacement = _FakeAsyncClient()
    pubsub._aclient = replacement
    assert await pubsub.aresubscribe() == 2
    assert replacement.subscriptions == {_prefix("a"), _prefix("b")}
