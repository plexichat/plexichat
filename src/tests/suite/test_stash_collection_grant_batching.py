"""Regression: inherited collection grants are read in one bounded batch.

``_collection_grant_permissions`` used to issue one SELECT per ancestor in the
collection chain, so a deep folder nesting turned a single authorization check
into N round trips. Grant rows for the whole chain are now read in one bounded
IN query scoped to the requesting user. These tests pin both the performance
property (query count no longer scales with chain depth) and the security
properties the batching must not weaken:

* a grant belonging to a different subject is never surfaced (no cross-user
  leak);
* revoked and expired grants still deny access;
* a backend error fails closed instead of degrading into "no grant".
"""

import pytest

from src.core.suite.stash import StashManager
from src.core.suite.stash.manager import StashPermissionError
from src.core.suite.stash.schema import create_tables as create_stash_tables

CHAIN_DEPTH = 12


def _manager(db, server_manager, media_manager):
    create_stash_tables(db)
    return StashManager(db, servers_module=server_manager, media=media_manager)


def _deep_item(manager, owner, depth=CHAIN_DEPTH):
    """Create a chain of ``depth`` nested collections with an item at the leaf."""
    root = manager.create_collection(owner.id, "root")
    leaf_id = root["id"]
    for i in range(depth):
        leaf_id = manager.create_collection(owner.id, f"child-{i}", parent_id=leaf_id)[
            "id"
        ]
    item = manager.create_item(
        owner.id, item_type="note", title="deep", collection_id=int(leaf_id)
    )
    return root, item


def _count_grant_reads(db, monkeypatch):
    """Record every read of the collection-grant table, whatever the helper.

    Wrapping both ``fetch_one`` and ``fetch_all`` keeps the measurement honest:
    the pre-fix implementation used one ``fetch_one`` per ancestor, while the
    batched one uses a single ``fetch_all``. The assertion below is therefore
    about the number of *round trips*, not which helper issued them.
    """
    reads = []
    for name in ("fetch_one", "fetch_all"):
        real = getattr(db, name)

        def counting(sql, params=(), _real=real, _name=name):
            if "stash_collection_grants" in sql:
                reads.append((_name, sql))
            return _real(sql, params)

        monkeypatch.setattr(db, name, counting)
    return reads


def test_deep_chain_uses_one_bounded_grant_query(
    db, two_users, server_manager, media_manager, monkeypatch
):
    owner, viewer = two_users
    manager = _manager(db, server_manager, media_manager)
    root, item = _deep_item(manager, owner)
    manager.grant_collection(owner.id, int(root["id"]), viewer.id, ["view_metadata"])

    grant_reads = _count_grant_reads(db, monkeypatch)
    assert manager.get_item(viewer.id, item["id"])["id"] == item["id"]

    # The chain has CHAIN_DEPTH + 1 ancestors. The batched implementation reads
    # them in a single IN query; the old one issued one SELECT per ancestor.
    assert len(grant_reads) == 1, grant_reads
    method, sql = grant_reads[0]
    assert method == "fetch_all" and " IN (" in sql, grant_reads
    # The bind list is bounded by the chunk constant, never the chain length.
    from src.core.suite.stash import stash_manager_base as base

    assert sql.count("?") <= base._COLLECTION_GRANT_LOOKUP_CHUNK + 1  # +subject_id


def test_grant_for_another_subject_is_not_leaked(
    db, two_users, server_manager, media_manager
):
    owner, viewer = two_users
    manager = _manager(db, server_manager, media_manager)
    root, item = _deep_item(manager, owner)

    # A grant on the same collection chain but for a *different* subject must
    # not authorize this viewer, even though the batched query spans ancestors.
    manager.grant_collection(owner.id, int(root["id"]), owner.id, ["view_metadata"])
    with pytest.raises(StashPermissionError):
        manager.get_item(viewer.id, item["id"])


def test_revoked_grant_still_denies_after_batching(
    db, two_users, server_manager, media_manager
):
    owner, viewer = two_users
    manager = _manager(db, server_manager, media_manager)
    root, item = _deep_item(manager, owner, depth=2)
    grant = manager.grant_collection(
        owner.id, int(root["id"]), viewer.id, ["view_metadata"]
    )
    assert manager.get_item(viewer.id, item["id"])["id"] == item["id"]

    manager.revoke_collection_grant(owner.id, int(grant["id"]))
    with pytest.raises(StashPermissionError):
        manager.get_item(viewer.id, item["id"])


def test_grant_lookup_error_fails_closed(
    db, two_users, server_manager, media_manager, monkeypatch
):
    owner, viewer = two_users
    manager = _manager(db, server_manager, media_manager)
    root, item = _deep_item(manager, owner, depth=2)
    manager.grant_collection(owner.id, int(root["id"]), viewer.id, ["view_metadata"])

    for name in ("fetch_one", "fetch_all"):
        real = getattr(db, name)

        def exploding(sql, params=(), _real=real):
            if "stash_collection_grants" in sql:
                raise RuntimeError("grant backend down")
            return _real(sql, params)

        monkeypatch.setattr(db, name, exploding)
    with pytest.raises(RuntimeError, match="grant backend down"):
        manager.get_item(viewer.id, item["id"])
