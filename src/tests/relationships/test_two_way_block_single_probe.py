"""``is_blocked_by_either`` must resolve both directions in one DB probe.

The two-way block check sits on several hot paths (DM create/send, presence,
reaction permission checks) and is also invoked once per other participant by
DM fan-out. Its old implementation issued two sequential ``fetch_one`` calls -
one per direction - so the common "nobody blocked anybody" case paid twice the
authoritative round trips.

This guard pins both the query budget and the security contract: the combined
predicate still detects a block in either direction, is scoped to exactly the
two ids under test (no cross-user leakage), and propagates DB errors so callers
continue to fail closed.
"""

import pytest


@pytest.mark.relationships
class TestTwoWayBlockSingleProbe:
    @staticmethod
    def _count_block_probes(rel_manager):
        """Wrap fetch_one and count calls touching the authoritative table."""
        real_fetch_one = rel_manager._db.fetch_one
        calls = []

        def spy(query, *args, **kwargs):
            if "rel_blocked" in str(query):
                calls.append((str(query), args))
            return real_fetch_one(query, *args, **kwargs)

        rel_manager._db.fetch_one = spy
        return calls

    def test_unblocked_pair_costs_one_probe(self, rel_manager, two_users):
        user1, user2 = two_users
        calls = self._count_block_probes(rel_manager)

        assert rel_manager.is_blocked_by_either(user1.id, user2.id) is False
        assert len(calls) == 1, (
            "two-way block check must use one combined query, "
            f"got {len(calls)}: {calls}"
        )
        # The single statement carries both user ids and is scoped to them.
        sql, args = calls[0]
        bound = args[0] if args and isinstance(args[0], (tuple, list)) else args
        assert "OR" in sql.upper()
        assert int(user1.id) in [int(p) for p in bound]
        assert int(user2.id) in [int(p) for p in bound]

    def test_block_in_either_direction_is_still_detected(self, rel_manager, two_users):
        user1, user2 = two_users
        self._count_block_probes(rel_manager)

        rel_manager.block_user(user1.id, user2.id)
        assert rel_manager.is_blocked_by_either(user1.id, user2.id) is True
        assert rel_manager.is_blocked_by_either(user2.id, user1.id) is True

    def test_probe_error_propagates_for_fail_closed_callers(
        self, rel_manager, two_users, monkeypatch
    ):
        user1, user2 = two_users

        def broken(query, *args, **kwargs):
            if "rel_blocked" in str(query):
                raise RuntimeError("database unavailable")
            raise AssertionError("unexpected query")

        monkeypatch.setattr(rel_manager._db, "fetch_one", broken)
        with pytest.raises(RuntimeError, match="database unavailable"):
            rel_manager.is_blocked_by_either(user1.id, user2.id)
