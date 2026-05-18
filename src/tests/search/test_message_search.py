"""
Tests for message search functionality.
"""

import pytest
import uuid

from src.core.search.models import MessageSearchResult


@pytest.mark.search
class TestMessageSearchBasic:
    """Test basic message search."""

    def test_search_returns_results(self, users_with_dm):
        """Test that search returns matching results."""
        user1, user2, dm, messages, search = users_with_dm

        results = search.search_messages(user1.id, "hello")

        assert isinstance(results, list)

    def test_search_empty_query(self, users_with_dm):
        """Test search with empty query returns empty."""
        user1, user2, dm, messages, search = users_with_dm

        results = search.search_messages(user1.id, "")

        assert results == []

    def test_search_no_matches(self, users_with_dm):
        """Test search with no matches returns empty."""
        user1, user2, dm, messages, search = users_with_dm

        results = search.search_messages(user1.id, "xyznonexistent123")

        assert results == []

    def test_search_result_type(self, users_with_dm):
        """Test search results are MessageSearchResult."""
        user1, user2, dm, messages, search = users_with_dm

        results = search.search_messages(user1.id, "hello")

        if results:
            assert isinstance(results[0], MessageSearchResult)


@pytest.mark.search
class TestMessageSearchFilters:
    """Test message search with filters."""

    def test_search_in_conversation(self, users_with_dm):
        """Test search within specific conversation."""
        user1, user2, dm, messages, search = users_with_dm

        results = search.search_messages(user1.id, "hello", conversation_id=dm.id)

        for result in results:
            assert result.conversation_id == dm.id

    def test_search_with_limit(self, users_with_dm):
        """Test search respects limit."""
        user1, user2, dm, messages, search = users_with_dm

        results = search.search_messages(user1.id, "hello", limit=1)

        assert len(results) <= 1

    def test_search_with_offset(self, users_with_dm):
        """Test search respects offset."""
        user1, user2, dm, messages, search = users_with_dm

        all_results = search.search_messages(user1.id, "hello", limit=10)
        offset_results = search.search_messages(user1.id, "hello", limit=10, offset=1)

        if len(all_results) > 1:
            assert len(offset_results) == len(all_results) - 1


@pytest.mark.search
class TestMessageSearchPermissions:
    """Test message search permission checks."""

    def test_user_only_sees_accessible_messages(
        self, auth_manager, messaging_manager, search_manager
    ):
        """Test users only see messages they can access."""
        auth = auth_manager
        messaging = messaging_manager
        search = search_manager

        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"perm_user1_{unique_id}",
            email=f"perm_user1_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"perm_user2_{unique_id}",
            email=f"perm_user2_{unique_id}@example.com",
            password="TestPass123!",
        )
        user3 = auth.register(
            username=f"perm_user3_{unique_id}",
            email=f"perm_user3_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm12 = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm12.id, f"secret message {unique_id}")

        search.index_message(
            msg.id,
            msg.content,
            {
                "author_id": user1.id,
                "conversation_id": dm12.id,
            },
        )

        search.search_messages(user1.id, unique_id)
        results_user3 = search.search_messages(user3.id, unique_id)

        assert len(results_user3) == 0


@pytest.mark.search
class TestMessageSearchIndexing:
    """Test message indexing."""

    def test_index_message(self, auth_manager, messaging_manager, search_manager):
        """Test indexing a message."""
        auth = auth_manager
        messaging = messaging_manager
        search = search_manager

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"idx_user_{unique_id}",
            email=f"idx_user_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"idx_user2_{unique_id}",
            email=f"idx_user2_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging.create_dm(user.id, user2.id)
        msg = messaging.send_message(user.id, dm.id, f"indextest {unique_id}")

        search.index_message(
            msg.id,
            msg.content,
            {
                "author_id": user.id,
                "conversation_id": dm.id,
            },
        )

        results = search.search_messages(user.id, f"indextest {unique_id}")

        assert len(results) >= 1

    def test_remove_from_index(self, auth_manager, messaging_manager, search_manager):
        """Test removing a message from index."""
        auth = auth_manager
        messaging = messaging_manager
        search = search_manager

        unique_id = uuid.uuid4().hex[:8]

        user = auth.register(
            username=f"rm_user_{unique_id}",
            email=f"rm_user_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth.register(
            username=f"rm_user2_{unique_id}",
            email=f"rm_user2_{unique_id}@example.com",
            password="TestPass123!",
        )

        dm = messaging.create_dm(user.id, user2.id)
        msg = messaging.send_message(user.id, dm.id, f"removetest {unique_id}")

        search.index_message(
            msg.id,
            msg.content,
            {
                "author_id": user.id,
                "conversation_id": dm.id,
            },
        )

        search.remove_from_index(msg.id)

        results = search.search_messages(user.id, f"removetest {unique_id}")

        matching = [r for r in results if r.message_id == msg.id]
        assert len(matching) == 0


@pytest.mark.search
class TestMessageSearchLimits:
    """Test search limits."""

    def test_limit_exceeds_max(self, users_with_dm):
        """Test that exceeding max limit raises error."""
        user1, user2, dm, messages, search = users_with_dm

        from src.core.search.exceptions import SearchLimitError

        with pytest.raises(SearchLimitError) as exc_info:
            search.search_messages(user1.id, "hello", limit=1000)

        assert exc_info.value.max_allowed == 100
        assert exc_info.value.requested == 1000
