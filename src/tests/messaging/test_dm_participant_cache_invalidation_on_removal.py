"""Regression: a removed DM/group participant must not outlive in the cached audience.

``EventRouter._get_dm_participant_ids`` memoizes the fully filtered DM/group
participant audience for a short epoch. A direct or bulk participant removal
used to delete the member row but leave that memoized audience intact, so private
conversation events (message bodies, typing, reactions) kept being routed to a
user who was no longer a participant until the cache TTL lapsed.

Both authoritative removal paths in ``ParticipantService`` now retire the cached
audience. These tests fail against the pre-fix code: the removed user stays in
the resolved audience.
"""

import pytest

from src.core.events.router import EventRouter, clear_dm_participant_cache

pytestmark = pytest.mark.messaging


@pytest.fixture(autouse=True)
def _clean_dm_audience_cache():
    clear_dm_participant_cache()
    yield
    clear_dm_participant_cache()


def _router(messaging_manager):
    return EventRouter(
        messaging_module=messaging_manager,
        relationships_module=None,
    )


def test_removed_participant_leaves_cached_audience(messaging_manager, three_users):
    """Direct removal: the memoized audience must not retain the removed user."""
    owner, victim, third = three_users
    group = messaging_manager.create_group(
        owner.id, "cache-evict", [victim.id, third.id]
    )
    router = _router(messaging_manager)

    # Warm the audience while the victim is still a member.
    before = router._get_dm_participant_ids(group.id, owner.id)
    assert victim.id in before

    assert messaging_manager.remove_participant(owner.id, group.id, victim.id)
    assert victim.id not in messaging_manager.get_participant_ids(group.id)

    # Fresh resolution must reflect the removal, not the cached pre-removal set.
    after = router._get_dm_participant_ids(group.id, owner.id)
    assert victim.id not in after, (
        f"removed participant {victim.id} still routed via cached audience {after}"
    )
    assert owner.id in after and third.id in after


def test_bulk_removed_participant_leaves_cached_audience(
    messaging_manager, three_users
):
    """Bulk removal (server kick/ban) must also retire the memoized audience."""
    owner, victim, third = three_users
    group = messaging_manager.create_group(
        owner.id, "cache-evict-bulk", [victim.id, third.id]
    )
    router = _router(messaging_manager)

    before = router._get_dm_participant_ids(group.id, owner.id)
    assert victim.id in before

    messaging_manager.remove_participant_from_conversations(victim.id, [group.id])
    assert victim.id not in messaging_manager.get_participant_ids(group.id)

    after = router._get_dm_participant_ids(group.id, owner.id)
    assert victim.id not in after, (
        f"bulk-removed participant {victim.id} still routed via cached audience {after}"
    )
