"""
Tests for search result ranking.
"""

import pytest
from datetime import datetime, timedelta

from src.core.search.query.ranking import RankingEngine, rank_results, RankingWeights
from src.core.search.models import (
    ParsedQuery,
    MessageSearchResult,
    UserSearchResult,
    ServerSearchResult,
)


@pytest.mark.search
class TestMessageRanking:
    """Test message result ranking."""

    def test_ranking_by_score(self):
        """Test results are sorted by score."""
        results = [
            MessageSearchResult(
                id=1,
                message_id=1,
                content="low",
                score=1.0,
                author_id=1,
                conversation_id=1,
            ),
            MessageSearchResult(
                id=2,
                message_id=2,
                content="high",
                score=5.0,
                author_id=1,
                conversation_id=1,
            ),
            MessageSearchResult(
                id=3,
                message_id=3,
                content="medium",
                score=3.0,
                author_id=1,
                conversation_id=1,
            ),
        ]

        parsed = ParsedQuery(raw_query="test", search_terms=["test"])

        engine = RankingEngine()
        ranked = engine.rank_message_results(results, parsed)

        assert ranked[0].score >= ranked[1].score >= ranked[2].score

    def test_exact_phrase_boost(self):
        """Test exact phrase match boosts score."""
        results = [
            MessageSearchResult(
                id=1,
                message_id=1,
                content="hello world",
                score=1.0,
                author_id=1,
                conversation_id=1,
            ),
            MessageSearchResult(
                id=2,
                message_id=2,
                content="hello there",
                score=1.0,
                author_id=1,
                conversation_id=1,
            ),
        ]

        parsed = ParsedQuery(
            raw_query='"hello world"', search_terms=[], exact_phrases=["hello world"]
        )

        engine = RankingEngine()
        ranked = engine.rank_message_results(results, parsed)

        hello_world = next(r for r in ranked if "world" in r.content)
        hello_there = next(r for r in ranked if "there" in r.content)

        assert hello_world.score > hello_there.score

    def test_recency_boost(self):
        """Test recent messages get boosted."""
        now = datetime.utcnow()
        old_ts = int((now - timedelta(days=30)).timestamp() * 1000)
        new_ts = int(now.timestamp() * 1000)

        results = [
            MessageSearchResult(
                id=1,
                message_id=1,
                content="old",
                score=1.0,
                author_id=1,
                conversation_id=1,
                created_at=old_ts,
            ),
            MessageSearchResult(
                id=2,
                message_id=2,
                content="new",
                score=1.0,
                author_id=1,
                conversation_id=1,
                created_at=new_ts,
            ),
        ]

        parsed = ParsedQuery(raw_query="test", search_terms=["test"])

        engine = RankingEngine()
        ranked = engine.rank_message_results(results, parsed, now_ms=new_ts)

        new_msg = next(r for r in ranked if r.content == "new")
        old_msg = next(r for r in ranked if r.content == "old")

        assert new_msg.score > old_msg.score

    def test_pinned_boost(self):
        """Test pinned messages get boosted."""
        results = [
            MessageSearchResult(
                id=1,
                message_id=1,
                content="pinned",
                score=1.0,
                author_id=1,
                conversation_id=1,
                is_pinned=True,
            ),
            MessageSearchResult(
                id=2,
                message_id=2,
                content="not pinned",
                score=1.0,
                author_id=1,
                conversation_id=1,
                is_pinned=False,
            ),
        ]

        parsed = ParsedQuery(raw_query="test", search_terms=["test"])

        engine = RankingEngine()
        ranked = engine.rank_message_results(results, parsed)

        pinned = next(r for r in ranked if r.is_pinned)
        not_pinned = next(r for r in ranked if not r.is_pinned)

        assert pinned.score > not_pinned.score

    def test_empty_results(self):
        """Test ranking empty results."""
        engine = RankingEngine()
        parsed = ParsedQuery(raw_query="test", search_terms=["test"])

        ranked = engine.rank_message_results([], parsed)

        assert ranked == []


@pytest.mark.search
class TestUserRanking:
    """Test user result ranking."""

    def test_exact_username_match(self):
        """Test exact username match gets highest score."""
        results = [
            UserSearchResult(id=1, user_id=1, username="alice", score=1.0),
            UserSearchResult(id=2, user_id=2, username="alice_smith", score=1.0),
            UserSearchResult(id=3, user_id=3, username="bob_alice", score=1.0),
        ]

        engine = RankingEngine()
        ranked = engine.rank_user_results(results, "alice", user_id=0)

        assert ranked[0].username == "alice"

    def test_username_prefix_match(self):
        """Test username prefix match ranks higher than contains."""
        results = [
            UserSearchResult(id=1, user_id=1, username="alice_smith", score=1.0),
            UserSearchResult(id=2, user_id=2, username="bob_alice", score=1.0),
        ]

        engine = RankingEngine()
        ranked = engine.rank_user_results(results, "alice", user_id=0)

        assert ranked[0].username == "alice_smith"

    def test_display_name_match(self):
        """Test display name matching."""
        results = [
            UserSearchResult(
                id=1, user_id=1, username="user1", display_name="Alice", score=1.0
            ),
            UserSearchResult(
                id=2, user_id=2, username="user2", display_name="Bob", score=1.0
            ),
        ]

        engine = RankingEngine()
        ranked = engine.rank_user_results(results, "alice", user_id=0)

        alice = next(r for r in ranked if r.display_name == "Alice")
        bob = next(r for r in ranked if r.display_name == "Bob")

        assert alice.score > bob.score

    def test_mutual_servers_boost(self):
        """Test mutual servers boost score."""
        results = [
            UserSearchResult(
                id=1, user_id=1, username="alice", score=1.0, mutual_servers=5
            ),
            UserSearchResult(
                id=2, user_id=2, username="alice2", score=1.0, mutual_servers=0
            ),
        ]

        engine = RankingEngine()
        ranked = engine.rank_user_results(results, "alice", user_id=0)

        with_mutual = next(r for r in ranked if r.mutual_servers > 0)
        without_mutual = next(r for r in ranked if r.mutual_servers == 0)

        assert with_mutual.score > without_mutual.score

    def test_mutual_friends_boost(self):
        """Test mutual friends boost score."""
        results = [
            UserSearchResult(
                id=1, user_id=1, username="alice", score=1.0, mutual_friends=3
            ),
            UserSearchResult(
                id=2, user_id=2, username="alice2", score=1.0, mutual_friends=0
            ),
        ]

        engine = RankingEngine()
        ranked = engine.rank_user_results(results, "alice", user_id=0)

        with_friends = next(r for r in ranked if r.mutual_friends > 0)
        without_friends = next(r for r in ranked if r.mutual_friends == 0)

        assert with_friends.score > without_friends.score


@pytest.mark.search
class TestServerRanking:
    """Test server result ranking."""

    def test_exact_name_match(self):
        """Test exact server name match gets highest score."""
        results = [
            ServerSearchResult(id=1, server_id=1, name="Gaming", score=1.0),
            ServerSearchResult(id=2, server_id=2, name="Gaming Community", score=1.0),
            ServerSearchResult(id=3, server_id=3, name="Pro Gaming Hub", score=1.0),
        ]

        engine = RankingEngine()
        ranked = engine.rank_server_results(results, "gaming")

        assert ranked[0].name == "Gaming"

    def test_member_count_boost(self):
        """Test higher member count boosts score."""
        results = [
            ServerSearchResult(
                id=1, server_id=1, name="Small Gaming", score=1.0, member_count=10
            ),
            ServerSearchResult(
                id=2, server_id=2, name="Big Gaming", score=1.0, member_count=10000
            ),
        ]

        engine = RankingEngine()
        ranked = engine.rank_server_results(results, "gaming")

        big = next(r for r in ranked if r.member_count > 1000)
        small = next(r for r in ranked if r.member_count < 100)

        assert big.score > small.score

    def test_verified_boost(self):
        """Test verified servers get boosted."""
        results = [
            ServerSearchResult(
                id=1, server_id=1, name="Gaming 1", score=1.0, is_verified=True
            ),
            ServerSearchResult(
                id=2, server_id=2, name="Gaming 2", score=1.0, is_verified=False
            ),
        ]

        engine = RankingEngine()
        ranked = engine.rank_server_results(results, "gaming")

        verified = next(r for r in ranked if r.is_verified)
        not_verified = next(r for r in ranked if not r.is_verified)

        assert verified.score > not_verified.score

    def test_tag_match_boost(self):
        """Test tag matches boost score."""
        results = [
            ServerSearchResult(
                id=1,
                server_id=1,
                name="Server 1",
                score=1.0,
                tags=["minecraft", "survival"],
            ),
            ServerSearchResult(
                id=2, server_id=2, name="Server 2", score=1.0, tags=["other"]
            ),
        ]

        engine = RankingEngine()
        ranked = engine.rank_server_results(results, "minecraft")

        with_tag = next(r for r in ranked if "minecraft" in r.tags)
        without_tag = next(r for r in ranked if "minecraft" not in r.tags)

        assert with_tag.score > without_tag.score


@pytest.mark.search
class TestRankingWeights:
    """Test custom ranking weights."""

    def test_custom_weights(self):
        """Test custom ranking weights."""
        results = [
            MessageSearchResult(
                id=1,
                message_id=1,
                content="pinned",
                score=1.0,
                author_id=1,
                conversation_id=1,
                is_pinned=True,
            ),
            MessageSearchResult(
                id=2,
                message_id=2,
                content="not pinned",
                score=1.0,
                author_id=1,
                conversation_id=1,
                is_pinned=False,
            ),
        ]

        parsed = ParsedQuery(raw_query="test", search_terms=["test"])

        weights = RankingWeights(pinned=10.0)
        engine = RankingEngine(weights)
        ranked = engine.rank_message_results(results, parsed)

        pinned = next(r for r in ranked if r.is_pinned)
        not_pinned = next(r for r in ranked if not r.is_pinned)

        assert pinned.score - not_pinned.score >= 9.0


@pytest.mark.search
class TestRankResultsConvenience:
    """Test rank_results convenience function."""

    def test_rank_message_results(self):
        """Test ranking message results via convenience function."""
        results = [
            MessageSearchResult(
                id=1,
                message_id=1,
                content="test",
                score=1.0,
                author_id=1,
                conversation_id=1,
            ),
        ]

        parsed = ParsedQuery(raw_query="test", search_terms=["test"])

        ranked = rank_results(results, parsed_query=parsed)

        assert len(ranked) == 1

    def test_rank_user_results(self):
        """Test ranking user results via convenience function."""
        results = [
            UserSearchResult(id=1, user_id=1, username="alice", score=1.0),
        ]

        ranked = rank_results(results, query="alice", user_id=1)

        assert len(ranked) == 1

    def test_rank_server_results(self):
        """Test ranking server results via convenience function."""
        results = [
            ServerSearchResult(id=1, server_id=1, name="Gaming", score=1.0),
        ]

        ranked = rank_results(results, query="gaming")

        assert len(ranked) == 1
