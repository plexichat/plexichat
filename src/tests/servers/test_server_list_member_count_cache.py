"""Regression + measurement tests for the bounded server-list member-count cache.

``ServerOpsMixin.get_servers`` used to re-run a correlated
``SELECT COUNT(*) FROM srv_members`` for every server in the listing, on every
call. The count is now memoized in a bounded, short-TTL per-server cache that is
invalidated on every membership mutation. These tests pin three properties:

1. the repeated-count work drops on warm listings (measured as the number of
   ``srv_members`` COUNT statements actually issued);
2. the cache is a hard-bounded LRU (entry/byte/TTL limits), not a plain dict;
3. join/leave invalidate it so a listing never serves a stale count.
"""

from __future__ import annotations

import re
import uuid

import pytest

from src.core.bounded_lru import BoundedLRU
from src.core.database import invalidate_pattern
from src.core.servers.manager import member_count_cache as mcc


@pytest.fixture(autouse=True)
def _clean_member_count_cache():
    mcc.invalidate_member_count_cache()
    yield
    mcc.invalidate_member_count_cache()


def _uid():
    return uuid.uuid4().hex[:10]


def _clear_db_query_cache(db):
    """Drop the DB's own short-lived read cache so counts reflect writes now.

    Direct ``db.execute`` writes do not invalidate that tier, so a test that
    writes then immediately reads must clear it explicitly (the same pattern
    the existing query-count regressions use).
    """
    with db._query_cache_lock:
        db._query_cache.clear()


class _FakeDB:
    """Minimal ``fetch_all`` stand-in returning one count row per requested id."""

    def __init__(self, counts):
        self.counts = dict(counts)
        self.calls = 0

    def fetch_all(self, query, params=None):
        self.calls += 1
        assert "FROM srv_members" in query
        assert "COUNT(*)" in query
        assert "GROUP BY" in query
        return [
            {"server_id": sid, "c": self.counts.get(sid, 0)} for sid in (params or ())
        ]


class _SQLSpy:
    """Record SQL passed to ``fetch_all``/``fetch_one`` on the live DB object."""

    def __init__(self, db):
        self.db = db
        self.statements = []

    def _wrap(self, real):
        def wrapped(query, params=None, *args, **kwargs):
            self.statements.append(str(query))
            return real(query, params, *args, **kwargs)

        return wrapped

    def __enter__(self):
        self._orig_all = self.db.fetch_all
        self._orig_one = self.db.fetch_one
        self.db.fetch_all = self._wrap(self._orig_all)
        self.db.fetch_one = self._wrap(self._orig_one)
        return self

    def __exit__(self, *exc):
        self.db.fetch_all = self._orig_all
        self.db.fetch_one = self._orig_one

    def member_count_statements(self):
        # Mentioning srv_members is not the same as counting members: the
        # listing query itself carries the membership EXISTS (and its own
        # COUNTs for channels and roles), so matching on those two words
        # counted the listing too. Once the join was replaced by an EXISTS the
        # cold listing issued 2 statements and five warm listings issued 5, and
        # the warm comparison below could never hold. Match only statements
        # that aggregate srv_members, which is what "recounts members" means.
        # ``[^()]*`` keeps the match inside one scalar subquery: the listing has
        # its own ``COUNT(*) FROM srv_channels`` earlier in the same statement,
        # and a looser span walked from that count to the membership EXISTS.
        pattern = re.compile(r"COUNT\(\*\)[^()]*FROM\s+srv_members", re.IGNORECASE)
        return [s for s in self.statements if pattern.search(s)]


def _add_members(servers, auth_manager, server_id, n):
    for _ in range(n):
        tag = _uid()
        user = auth_manager.register(
            username=f"mc_{tag}",
            email=f"mc_{tag}@example.test",
            password="MemberCount123!",
        )
        servers.add_member(server_id, user.id)


class TestMemberCountCacheUnit:
    def test_batches_misses_and_reuses_warm_entries(self):
        db = _FakeDB({1: 5, 2: 9, 3: 0})
        first = mcc.prime_member_counts(db, [1, 2, 3])
        assert first == {1: 5, 2: 9, 3: 0}
        assert db.calls == 1  # all three misses resolved in one query

        second = mcc.prime_member_counts(db, [1, 2, 3])
        assert second == first
        assert db.calls == 1  # warm reads issued no backend query

    def test_is_a_hard_bounded_lru(self):
        cache = mcc._member_count_cache
        assert isinstance(cache, BoundedLRU)
        stats = mcc.member_count_cache_stats()
        assert stats["max_entries"] > 0
        assert stats["max_bytes"] > 0
        assert cache._ttl > 0

        # Churn far more distinct ids than the entry ceiling: the cache must
        # evict rather than grow without bound.
        db = _FakeDB({sid: sid for sid in range(6000)})
        mcc.prime_member_counts(db, list(range(6000)))
        after = mcc.member_count_cache_stats()
        assert after["size"] <= after["max_entries"]
        assert after["evictions"] > 0

    def test_ttl_expires_entries(self, monkeypatch):
        import src.core.bounded_lru as bounded_lru_mod

        class _Clock:
            now = 100.0

            def monotonic(self):
                return self.now

        clock = _Clock()
        monkeypatch.setattr(bounded_lru_mod, "time", clock)

        db = _FakeDB({42: 7})
        assert mcc.prime_member_counts(db, [42]) == {42: 7}
        assert mcc.get_cached_member_count(42) == 7

        clock.now += mcc.MEMBER_COUNT_TTL_SECONDS + 1
        assert mcc.get_cached_member_count(42) is None


class TestServerListMemberCountCache:
    def test_repeated_listings_reuse_cached_counts(
        self, db, server_manager, auth_manager, test_server
    ):
        """Warm listings stop re-issuing the per-server member COUNT."""
        server, owner = test_server
        _add_members(server_manager, auth_manager, server.id, 4)

        def listing():
            # Force the @cached get_servers tier to re-run its body while the
            # member-count cache stays warm -- this is the repeated-listing case.
            _clear_db_query_cache(db)
            # Clear the whole @cached tier for get_servers so the body re-runs
            # while the member-count cache stays warm.
            invalidate_pattern("user_servers:*")
            return server_manager.get_servers(owner.id)

        mcc.invalidate_member_count_cache()
        with _SQLSpy(db) as cold_spy:
            cold = listing()
        cold_statements = len(cold_spy.member_count_statements())

        rounds = 5
        with _SQLSpy(db) as warm_spy:
            for _ in range(rounds):
                warm = listing()
        warm_statements = len(warm_spy.member_count_statements())

        # Each cold listing used to recount members (one correlated COUNT per
        # server); now a cold miss is a single batched COUNT and the next four
        # listings issue none.
        assert cold_statements >= 1
        assert warm_statements <= cold_statements, (
            f"member-count statements did not drop: "
            f"first listing={cold_statements}, {rounds} listings={warm_statements}"
        )
        assert warm_statements < rounds, (
            "warm listings still recompute per-server member counts"
        )
        assert {s.id: s.member_count for s in cold} == {
            s.id: s.member_count for s in warm
        }
        assert next(s for s in warm if s.id == server.id).member_count == 5

    def test_join_and_leave_invalidate_cached_count(
        self, db, server_manager, auth_manager, test_server
    ):
        """A listing never serves a stale count after a membership mutation."""
        server, owner = test_server
        server_id = server.id

        def count_for_owner():
            _clear_db_query_cache(db)
            invalidate_pattern("user_servers:*")
            row = next(
                s for s in server_manager.get_servers(owner.id) if s.id == server_id
            )
            return row.member_count

        # Seed the cache: owner is the only member.
        assert count_for_owner() == 1
        assert mcc.get_cached_member_count(server_id) == 1

        # Join via add_member.
        joiner = auth_manager.register(
            username=f"mc_join_{_uid()}",
            email=f"mc_join_{_uid()}@example.test",
            password="MemberCount123!",
        )
        server_manager.add_member(server_id, joiner.id)
        assert mcc.get_cached_member_count(server_id) is None  # invalidated
        assert count_for_owner() == 2

        # Voluntary leave via remove_member.
        server_manager.remove_member(joiner.id, server_id)
        assert mcc.get_cached_member_count(server_id) is None
        assert count_for_owner() == 1

        # Moderator kick via kick_member (also a leave).
        kickee = auth_manager.register(
            username=f"mc_kick_{_uid()}",
            email=f"mc_kick_{_uid()}@example.test",
            password="MemberCount123!",
        )
        server_manager.add_member(server_id, kickee.id)
        assert count_for_owner() == 2
        server_manager.kick_member(owner.id, server_id, kickee.id)
        assert mcc.get_cached_member_count(server_id) is None
        assert count_for_owner() == 1
