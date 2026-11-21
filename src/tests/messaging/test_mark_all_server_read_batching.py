"""Regression test: mark-all-channels-in-a-server must be batched.

BEFORE: ``MessageStatusService.mark_all_server_read`` looped over every
channel in the server and called the full ``mark_read`` once per channel. Each
call re-ran a participant lookup, MAX(id), settings/channel probes, a status
SELECT + executemany, several cache invalidations and a read-position write --
an O(N) fan-out that issued queries proportional to the number of channels.

AFTER: all channel conversation ids are resolved in one pass and handed to
``mark_read_bulk``, whose participation / last-read / MAX(id) / status-write /
read-position lookups are chunked into a bounded number of round trips. This
test measures the legacy loop and the batched path from a cold cache and pins
three properties: a large reduction, sub-linear cost, and cost independent of
the number of channels.
"""

from types import SimpleNamespace

import pytest

from src.core.database.cache.base import mem_cache_clear_pattern


N_CHANNELS = 20
SMALL_CHANNELS = 4


class _FakeServers:
    """Minimal servers module exposing just the channel lookups mark-read uses."""

    def __init__(self, conversation_ids):
        self._conversation_ids = list(conversation_ids)

    def get_channels(self, server_id, user_id):
        return [SimpleNamespace(conversation_id=cid) for cid in self._conversation_ids]

    def get_channel(self, conversation_id, user_id):
        return SimpleNamespace(read_receipts_enabled=True)


def _build_channels(messaging_manager, user_pool, reader_id, count):
    """Create ``count`` DMs, each with one unread message for ``reader_id``."""
    conv_ids = []
    for _ in range(count):
        other = user_pool.get_user()
        dm = messaging_manager.create_dm(reader_id, other.id)
        messaging_manager.send_message(other.id, dm.id, "unread probe")
        conv_ids.append(int(dm.id))
    return conv_ids


def _legacy_mark_all(messaging_manager, user_id, server_id):
    """The original per-channel implementation, kept as the query baseline."""
    channels = messaging_manager._message_status_svc._get_servers_module().get_channels(
        server_id, user_id
    )
    marked = 0
    for channel in channels:
        if getattr(channel, "conversation_id", None):
            try:
                messaging_manager.mark_read(user_id, channel.conversation_id)
                marked += 1
            except Exception:
                continue
    return marked


def _cold_query_count(db, svc, fn, *args):
    """Run ``fn`` from a cold cache and return its DB round-trip count."""
    mem_cache_clear_pattern("*")
    participant_svc = svc._participant_svc
    if hasattr(participant_svc, "_cache"):
        participant_svc._cache.clear()
    with db._query_cache_lock:
        db._query_cache.clear()
    db.reset_request_metrics()
    result = fn(*args)
    return db.get_request_metrics()["query_count"], result


@pytest.mark.messaging
def test_mark_all_server_read_query_count_is_bounded_by_batching(
    db, messaging_manager, user_pool, monkeypatch
):
    reader = user_pool.get_user()
    svc = messaging_manager._message_status_svc

    legacy_ids = _build_channels(messaging_manager, user_pool, reader.id, N_CHANNELS)
    batched_ids = _build_channels(messaging_manager, user_pool, reader.id, N_CHANNELS)
    small_ids = _build_channels(messaging_manager, user_pool, reader.id, SMALL_CHANNELS)

    holder = {"ids": legacy_ids}
    monkeypatch.setattr(svc, "_get_servers_module", lambda: _FakeServers(holder["ids"]))

    holder["ids"] = legacy_ids
    legacy_queries, legacy_marked = _cold_query_count(
        db, svc, _legacy_mark_all, messaging_manager, reader.id, 777
    )

    holder["ids"] = batched_ids
    batched_queries, batched_marked = _cold_query_count(
        db, svc, messaging_manager.mark_all_server_read, reader.id, 777
    )

    holder["ids"] = small_ids
    small_queries, small_marked = _cold_query_count(
        db, svc, messaging_manager.mark_all_server_read, reader.id, 777
    )

    print(
        "\nMARK_ALL_SERVER_READ_QUERY_COUNT "
        f"legacy={legacy_queries} batched={batched_queries} "
        f"small={small_queries} channels={N_CHANNELS}/{SMALL_CHANNELS}"
    )

    # All paths must actually process every channel.
    assert legacy_marked == N_CHANNELS
    assert batched_marked == N_CHANNELS
    assert small_marked == SMALL_CHANNELS

    # 1. A large, measurable reduction versus the per-channel loop. On the
    #    old code both numbers were ~100 (the same O(N) loop), so this fails
    #    without the fix.
    assert batched_queries * 3 < legacy_queries, (
        "mark_all_server_read reverted to a per-channel N+1: "
        f"{N_CHANNELS} channels -> legacy {legacy_queries} queries, "
        f"batched {batched_queries} queries"
    )

    # 2. Sub-linear: the batched path uses fewer round trips than channels.
    assert batched_queries < N_CHANNELS

    # 3. Bounded: cost does not grow with the channel count (allowing a small
    #    slack for chunk boundaries).
    assert batched_queries <= small_queries + 2, (
        "batched mark-all query count grew with channel count: "
        f"{SMALL_CHANNELS} channels -> {small_queries}, "
        f"{N_CHANNELS} channels -> {batched_queries}"
    )
