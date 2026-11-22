"""Thread membership writes must evict the cached channel-event audience.

``resolve_channel_event_recipients`` memoizes a channel's fully resolved
recipient list in ``_channel_recipient_cache`` keyed by the *server's RBAC
epoch*. A thread membership write (join / leave / add / remove) changes the
exact audience of that thread's channel-scoped events (typing, reactions,
read receipts, message activity) but does NOT bump the server epoch, so
without an explicit flush the cached list is still keyed to the old roster:
a member removed from a private thread stays in the warm recipient list for
the whole TTL and keeps receiving that thread's events after removal.

This regression proves the stale audience is replayed when no invalidation
happens and pins the fail-closed fix: every membership write evicts the cache,
mirroring the server-level membership funnel (``invalidate_member_count_cache``).
"""

import pytest

import src.api as api
from src.core import threads as threads_mod
from src.core.events import gateway_emit
from src.core.events.recipient_cache import RecipientResolutionCache


@pytest.mark.unit
def test_removed_thread_member_not_replayed_from_cached_audience(
    server_with_channel, monkeypatch
):
    from src.core.threads.models import ThreadType

    owner, member1, _member2, server, channel, servers, threads = server_with_channel

    # Private thread with owner + member1.
    thread = threads.create_thread(
        owner.id, channel.id, "secret planning", thread_type=ThreadType.PRIVATE
    )
    threads.add_member(owner.id, thread.id, member1.id)
    tid = thread.id

    # Wire the module-level gateway resolver to this test's threads manager.
    monkeypatch.setattr(threads_mod, "is_setup", lambda: True)
    monkeypatch.setattr(
        threads_mod,
        "get_thread_type_value",
        lambda x: ThreadType.PRIVATE.value if x == tid else None,
    )
    monkeypatch.setattr(
        threads_mod, "get_thread_member_user_ids", lambda x: threads.get_all_member_user_ids(x)
    )
    # No relationship module: the block filter is a no-op, so the audience is
    # exactly the live thread roster (minus the actor).
    monkeypatch.setattr(api, "get_relationships", lambda: None)
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: None)

    # Deterministic cache epoch so both resolutions use the same key.
    monkeypatch.setattr(
        gateway_emit,
        "_channel_recipient_cache",
        RecipientResolutionCache(clock=lambda: 100.0),
    )
    gateway_emit.clear_channel_recipient_cache()

    # Warm the cache while member1 is still in the roster. Actor is the owner,
    # so the resolved audience is exactly [member1].
    first = gateway_emit.resolve_channel_event_recipients(
        tid, guild_id=server.id, actor_id=owner.id
    )
    assert first == [member1.id]
    stats = gateway_emit.get_channel_recipient_cache_stats()
    assert stats["misses"] == 1  # the resolution was actually cached

    # Remove member1 through the real core membership path.
    assert threads.remove_member(owner.id, tid, member1.id) is True
    assert member1.id not in threads.get_all_member_user_ids(tid)  # live roster

    # The resolver must not replay the stale, wider audience. Without the
    # invalidation this returns [member1] from the warm cache and fails.
    second = gateway_emit.resolve_channel_event_recipients(
        tid, guild_id=server.id, actor_id=owner.id
    )
    assert member1.id not in second


@pytest.mark.unit
def test_stale_audience_is_replayed_without_invalidation(
    server_with_channel, monkeypatch
):
    """Control: prove the cache itself replays a stale audience.

    This bypasses the membership-write hook to show the leak the fix closes is
    real, not hypothetical: with the roster changed but the cache untouched,
    the warm list still contains the removed member.
    """
    from src.core.threads.models import ThreadType

    owner, member1, _member2, server, channel, servers, threads = server_with_channel

    thread = threads.create_thread(
        owner.id, channel.id, "secret planning", thread_type=ThreadType.PRIVATE
    )
    threads.add_member(owner.id, thread.id, member1.id)
    tid = thread.id

    members = {owner.id, member1.id}
    monkeypatch.setattr(threads_mod, "is_setup", lambda: True)
    monkeypatch.setattr(
        threads_mod,
        "get_thread_type_value",
        lambda x: ThreadType.PRIVATE.value if x == tid else None,
    )
    monkeypatch.setattr(
        threads_mod, "get_thread_member_user_ids", lambda x: sorted(members)
    )
    monkeypatch.setattr(api, "get_relationships", lambda: None)
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: None)

    monkeypatch.setattr(
        gateway_emit,
        "_channel_recipient_cache",
        RecipientResolutionCache(clock=lambda: 100.0),
    )
    gateway_emit.clear_channel_recipient_cache()

    first = gateway_emit.resolve_channel_event_recipients(
        tid, guild_id=server.id, actor_id=owner.id
    )
    assert first == [member1.id]

    # Change the roster WITHOUT clearing the cache (simulate a missed hook).
    members.discard(member1.id)
    assert member1.id not in threads_mod.get_thread_member_user_ids(tid)

    stale = gateway_emit.resolve_channel_event_recipients(
        tid, guild_id=server.id, actor_id=owner.id
    )
    # The cache replays the removed member: this is the vulnerable behaviour.
    assert stale == [member1.id]
