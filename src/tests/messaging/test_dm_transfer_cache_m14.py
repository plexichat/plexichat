"""Regression test: DM delete/leave invalidates the TRUE DM-lookup cache keys.

Agent 8 [M14] (stale-cache invalidation, wrong user after ownership
transfer): ``delete_conversation`` keyed DM-lookup invalidation off
``(user_id, conv.owner_id)``, but DMs are created with ``owner_id=NULL``,
so it evicted the phantom key ``dm_lookup:<user>:<user>`` and left the real
``dm_lookup:<u1>:<u2>`` entry stale -- DM-channel resolution kept mapping
the pair to the deleted conversation.

The fix resolves the true participant pair and evicts BOTH participant
mappings in EITHER user-pair ordering (plus per-user wildcards), so
list-DMs / DM-channel resolution reads fresh data post-transfer.

These tests FAIL on pre-fix code (the canonical pair key survives the
delete) and PASS post-fix.
"""

from unittest.mock import patch

from src.core.database import cache_get, cache_set


def _register(auth_manager, tag):
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            f"m14_{tag}", f"m14_{tag}@example.com", "TestPass123!"
        )


def _poison_lookup_cache(user_a_id, user_b_id, conv_id):
    lo, hi = (
        (user_a_id, user_b_id)
        if int(user_a_id) <= int(user_b_id)
        else (user_b_id, user_a_id)
    )
    cache_set(f"dm_lookup:{lo}:{hi}", conv_id, ttl=3600)
    cache_set(f"dm_lookup:{hi}:{lo}", conv_id, ttl=3600)
    return f"dm_lookup:{lo}:{hi}", f"dm_lookup:{hi}:{lo}"


def test_dm_delete_invalidates_both_pair_orderings(db, auth_manager, messaging_manager):
    user1 = _register(auth_manager, "del_a")
    user2 = _register(auth_manager, "del_b")

    dm = messaging_manager.create_dm(user1.id, user2.id)
    assert dm is not None

    # Simulate a warm production cache: BOTH orderings map to the DM.
    canon_key, rev_key = _poison_lookup_cache(user1.id, user2.id, dm.id)
    assert cache_get(canon_key) == dm.id
    assert cache_get(rev_key) == dm.id

    assert messaging_manager.delete_conversation(user1.id, dm.id) is True

    # Post-fix: neither the canonical nor the reversed pair key survives.
    # Pre-fix: the code evicted dm_lookup:<user>:<user>, so both survive.
    assert cache_get(canon_key) is None, (
        f"stale DM-lookup survived delete: {canon_key} still maps to {dm.id}"
    )
    assert cache_get(rev_key) is None, (
        f"stale reversed DM-lookup survived delete: {rev_key} still maps to {dm.id}"
    )

    # DB-level truth: the lookup row is gone ...
    svc = messaging_manager._conversation_svc
    assert svc._repo.get_dm_lookup(user1.id, user2.id) is None

    # ... list-DMs no longer shows the deleted conversation ...
    remaining_ids = [c.id for c in messaging_manager.get_conversations(user1.id)]
    assert dm.id not in remaining_ids

    # ... and re-creating the DM resolves FRESH (a new conversation), never
    # the deleted one, even with a re-warmed stale cache entry present.
    cache_set(canon_key, dm.id, ttl=3600)
    fresh = messaging_manager.create_dm(user1.id, user2.id)
    assert fresh is not None
    assert fresh.id != dm.id


def test_dm_leave_invalidates_pair_keys(db, auth_manager, messaging_manager):
    user1 = _register(auth_manager, "leave_a")
    user2 = _register(auth_manager, "leave_b")

    dm = messaging_manager.create_dm(user1.id, user2.id)
    assert dm is not None

    # DM leave delegates to delete_conversation (the DM ownership-transfer
    # path): the same pair-key eviction contract must hold.
    canon_key, rev_key = _poison_lookup_cache(user1.id, user2.id, dm.id)

    assert messaging_manager.leave_conversation(user1.id, dm.id) is True

    assert cache_get(canon_key) is None, (
        f"stale DM-lookup survived leave: {canon_key} still maps to {dm.id}"
    )
    assert cache_get(rev_key) is None, (
        f"stale reversed DM-lookup survived leave: {rev_key} still maps to {dm.id}"
    )

    svc = messaging_manager._conversation_svc
    assert svc._repo.get_dm_lookup(user1.id, user2.id) is None
    remaining_ids = [c.id for c in messaging_manager.get_conversations(user2.id)]
    assert dm.id not in remaining_ids
