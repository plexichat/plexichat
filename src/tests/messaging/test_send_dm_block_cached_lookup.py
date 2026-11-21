"""Per-send DM block gate must reuse the cached two-way block set.

``_enforce_dm_block_rules`` used to issue one authoritative ``rel_blocked``
probe per recipient on every DM send. With the relationships module wired,
that answer is already available from the cached two-way block set
(``get_all_blocked_ids``) that DM event fan-out also uses. This guard pins
the per-send probe budget to zero once the set is warm, while keeping both
block directions authoritative and failing closed in the module-down path.

Run with: pytest src/tests/messaging/test_send_dm_block_cached_lookup.py -q
"""

import pytest

from src.core.messaging.exceptions import ConversationAccessDeniedError


@pytest.fixture
def wired_relationships(db):
    """Expose a real relationships manager, then restore global state."""
    from src.core import relationships

    previous_manager = relationships._manager
    previous_setup = relationships._setup_complete
    relationships.setup(db)
    try:
        yield relationships
    finally:
        relationships._manager = previous_manager
        relationships._setup_complete = previous_setup


def _count_block_probes(db):
    """Count every authoritative query that touches ``rel_blocked``."""
    real_fetch_one = db.fetch_one
    real_fetch_all = db.fetch_all
    calls = []

    def spy_one(query, *args, **kwargs):
        if "rel_blocked" in str(query):
            calls.append(str(query))
        return real_fetch_one(query, *args, **kwargs)

    def spy_all(query, *args, **kwargs):
        if "rel_blocked" in str(query):
            calls.append(str(query))
        return real_fetch_all(query, *args, **kwargs)

    db.fetch_one = spy_one
    db.fetch_all = spy_all
    return calls


@pytest.mark.messaging
class TestSendDmBlockCachedLookup:
    def test_warm_block_set_makes_repeated_sends_probe_free(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Warm the cached two-way block set before measuring the send path.
        assert wired_relationships.get_all_blocked_ids(user1.id) == []

        probes = _count_block_probes(db)
        for _ in range(5):
            messaging_manager.send_message(user1.id, dm.id, "hello")

        print(f"\nRELBLOCKED PROBES FOR 5 SENDS: {len(probes)}")
        assert probes == [], (
            "warm DM sends must not re-probe rel_blocked; "
            f"got {len(probes)} probes: {probes[:3]}"
        )

    def test_block_in_either_direction_still_denies_send(
        self, db, messaging_manager, wired_relationships, two_users
    ):
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        wired_relationships.block_user(user2.id, user1.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(user1.id, dm.id, "blocked one way")
        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(user2.id, dm.id, "blocked other way")
