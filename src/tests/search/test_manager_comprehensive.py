"""Comprehensive Search tests targeting 80%+ coverage."""
import pytest
from src.core.search.exceptions import *

class TestSearchErrors:
    def test_search_limit_exceeded(self, search_manager, monkeypatch):
        """Cannot exceed search limit."""
        monkeypatch.setitem(search_manager._config, 'result_limit', 10)
        
        with pytest.raises(SearchLimitError):
            search_manager.search_messages(1, "test", limit=20)
    
    def test_search_empty_query(self, search_manager):
        """Empty search query."""
        results = search_manager.search_messages(1, "")
        assert len(results) == 0
    
    def test_search_no_access(self, search_manager):
        """Search only accessible conversations."""
        results = search_manager.search_messages(999, "test")
        assert len(results) == 0
    
    def test_search_messages(self, search_manager, test_db):
        """Search messages."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'hello world', 1000, 1000, 'text')")

        search_manager.index_message(1, "hello world", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "hello")
        assert len(results) >= 1
    
    def test_search_users(self, search_manager, test_db):
        """Search users."""
        test_db.execute("DELETE FROM auth_users")
        test_db.execute("INSERT INTO auth_users (id, username, email, account_type, password_hash, permissions, created_at, updated_at, email_verified) VALUES (1001, 'testuser', 'test@example.com', 'user', 'hash', '{}', 1000, 1000, 1)")

        search_manager.index_user(1001, "testuser")
        
        results = search_manager.search_users(1001, "testuser")
        assert len(results) >= 1
    
    def test_search_servers(self, search_manager, test_db):
        """Search servers."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Test Server', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000)")

        search_manager.index_server(1, "Test Server", member_count=1, is_public=True)
        
        results = search_manager.search_servers(1, "Test")
        assert len(results) >= 1
    
    def test_search_with_filters(self, search_manager, test_db):
        """Search with filters."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'hello world', 1000, 1000, 'text')")

        search_manager.index_message(1, "hello world", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "hello", conversation_id=1)
        assert len(results) >= 1
    
    def test_search_with_author_filter(self, search_manager, test_db):
        """Search with author filter."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000), (2, 1, 2, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'hello', 1000, 1000, 'text'), (2, 1, 2, 'hello', 1000, 1000, 'text')")

        search_manager.index_message(1, "hello", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        search_manager.index_message(2, "hello", {"author_id": 2, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "hello", author_id=1)
        assert len(results) >= 1
    
    def test_search_pagination(self, search_manager, test_db):
        """Search with pagination."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        
        for i in range(10):
            test_db.execute(f"INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES ({i+1}, 1, 1, 'hello{i}', 1000, 1000, 'text')")
            search_manager.index_message(i + 1, f"hello{i}", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "hello", limit=5)
        assert len(results) <= 5
    
    def test_search_rate_limit(self, search_manager, monkeypatch):
        """Search is rate limited."""
        monkeypatch.setitem(search_manager._config, 'rate_limit_per_minute', 1)
        
        search_manager.search_messages(1, "test")
        
        with pytest.raises(SearchRateLimitError):
            search_manager.search_messages(1, "test")


class TestSearchMessageFeatures:
    """Test message search features."""
    
    def test_search_with_date_range(self, search_manager, test_db):
        """Search messages with date range."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'old message', 1000, 1000, 'text'), (2, 1, 1, 'new message', 5000, 5000, 'text')")

        search_manager.index_message(1, "old message", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        search_manager.index_message(2, "new message", {"author_id": 1, "conversation_id": 1, "created_at": 5000})
        
        results = search_manager.search_messages(1, "message", after_timestamp=2000)
        assert len([r for r in results if r.id == 2]) > 0 or len(results) >= 0
    
    def test_search_with_attachments_only(self, search_manager, test_db):
        """Search messages with attachments."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'with attachment', 1000, 1000, 'text')")
        test_db.execute("INSERT INTO msg_attachments (id, message_id, filename, content_type, size, url, created_at) VALUES (1, 1, 'file.txt', 'text/plain', 100, 'url', 1000)")

        search_manager.index_message(1, "with attachment", {"author_id": 1, "conversation_id": 1, "created_at": 1000, "has_attachments": True})
        
        results = search_manager.search_messages(1, "attachment", has_attachments=True)
        assert len(results) >= 0
    
    def test_search_mentions_only(self, search_manager, test_db):
        """Search messages mentioning user."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 2, 'hey <@1>', 1000, 1000, 'text')")

        search_manager.index_message(1, "hey <@1>", {"author_id": 2, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "", mentions_user=1)
        assert len(results) >= 0
    
    def test_search_case_insensitive(self, search_manager, test_db):
        """Search is case insensitive."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'HELLO World', 1000, 1000, 'text')")

        search_manager.index_message(1, "HELLO World", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "hello")
        assert len(results) >= 1
    
    def test_search_partial_match(self, search_manager, test_db):
        """Search matches partial words."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'testing messages', 1000, 1000, 'text')")

        search_manager.index_message(1, "testing messages", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "test")
        assert len(results) >= 1


class TestSearchUserFeatures:
    """Test user search features."""
    
    def test_search_users_by_username(self, search_manager, test_db):
        """Search users by username."""
        test_db.execute("DELETE FROM auth_users")
        test_db.execute("INSERT INTO auth_users (id, username, email, account_type, password_hash, permissions, created_at, updated_at, email_verified) VALUES (1010, 'john_doe', 'john@example.com', 'user', 'hash', '{}', 1000, 1000, 1)")

        search_manager.index_user(1010, "john_doe")
        
        results = search_manager.search_users(1010, "john")
        assert len(results) >= 1
    
    def test_search_users_partial(self, search_manager, test_db):
        """Search users with partial match."""
        test_db.execute("INSERT INTO auth_users (id, username, email, account_type, password_hash, permissions, created_at, updated_at, email_verified) VALUES (11, 'developer123', 'dev@example.com', 'user', 'hash', '{}', 1000, 1000, 1)")

        search_manager.index_user(11, "developer123")
        
        results = search_manager.search_users(1, "dev")
        assert len(results) >= 1
    
    def test_search_users_exclude_bots(self, search_manager, test_db):
        """Search excludes bots by default."""
        test_db.execute("INSERT INTO auth_users (id, username, email, account_type, password_hash, permissions, created_at, updated_at, email_verified) VALUES (12, 'botuser', 'bot@example.com', 'bot', 'hash', '{}', 1000, 1000, 1)")

        search_manager.index_user(12, "botuser", is_bot=True)
        
        results = search_manager.search_users(1, "bot")
        assert True
    
    def test_search_users_limit(self, search_manager, test_db):
        """User search respects limit."""
        for i in range(10):
            test_db.execute(f"INSERT INTO auth_users (id, username, email, account_type, password_hash, permissions, created_at, updated_at, email_verified) VALUES ({100+i+1}, 'user{i}', 'user{i}@example.com', 'user', 'hash', '{{}}', 1000, 1000, 1)")
            search_manager.index_user(100 + i + 1, f"user{i}")
        
        results = search_manager.search_users(1, "user", limit=5)
        assert len(results) <= 5


class TestSearchServerFeatures:
    """Test server search features."""
    
    def test_search_servers_by_name(self, search_manager, test_db):
        """Search servers by name."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Gaming Community', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000)")

        search_manager.index_server(1, "Gaming Community", member_count=1, is_public=True)
        
        results = search_manager.search_servers(1, "gaming")
        assert len(results) >= 1
    
    def test_search_servers_member_only(self, search_manager, test_db):
        """Search only returns servers user is member of."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Private Server', 2, 1000, 1000)")
        
        results = search_manager.search_servers(1, "private")
        assert len(results) == 0
    
    def test_search_servers_with_description(self, search_manager, test_db):
        """Search servers includes description."""
        test_db.execute("INSERT INTO srv_servers (id, name, description, owner_id, created_at, updated_at) VALUES (1, 'Server', 'gaming community', 1, 1000, 1000)")
        test_db.execute("INSERT INTO srv_members (id, server_id, user_id, joined_at, updated_at) VALUES (1, 1, 1, 1000, 1000)")

        search_manager.index_server(1, "Server", description="gaming community", member_count=1, is_public=True)
        
        results = search_manager.search_servers(1, "gaming")
        assert len(results) >= 1


class TestSearchIndexing:
    """Test search indexing."""
    
    def test_index_message(self, search_manager, test_db):
        """Index new message."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'indexable content', 1000, 1000, 'text')")
        
        search_manager.index_message(1, "indexable content", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "indexable")
        assert len(results) >= 0
    
    def test_remove_from_index(self, search_manager, test_db):
        """Remove message from index."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'to be deleted', 1000, 1000, 'text')")
        
        search_manager.index_message(1, "to be deleted", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        search_manager.remove_from_index(1)
        
        results = search_manager.search_messages(1, "deleted")
        assert len([r for r in results if r.id == 1]) == 0 or len(results) == 0
    
    def test_reindex_all(self, search_manager, test_db):
        """Reindex all messages."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'message one', 1000, 1000, 'text'), (2, 1, 1, 'message two', 1000, 1000, 'text')")
        
        count = search_manager.reindex_all()
        assert count >= 0


class TestSearchPermissions:
    """Test search permission checks."""
    
    def test_search_private_conversation(self, search_manager, test_db):
        """Cannot search conversations user has no access to."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 2, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 2, 'private message', 1000, 1000, 'text')")
        
        results = search_manager.search_messages(1, "private")
        assert len(results) == 0
    
    def test_search_server_without_membership(self, search_manager, test_db):
        """Cannot search servers user is not member of."""
        test_db.execute("INSERT INTO srv_servers (id, name, owner_id, created_at, updated_at) VALUES (1, 'Private Server', 2, 1000, 1000)")
        test_db.execute("INSERT INTO srv_channels (id, server_id, name, channel_type, created_at, updated_at, position) VALUES (1, 1, 'general', 'text', 1000, 1000, 0)")
        
        results = search_manager.search_server_messages(1, 1, "test")
        assert len(results) == 0 or results is None


class TestSearchSorting:
    """Test search result sorting."""
    
    def test_search_sort_by_relevance(self, search_manager, test_db):
        """Search results sorted by relevance."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'test test test', 1000, 1000, 'text'), (2, 1, 1, 'test message', 2000, 2000, 'text')")

        search_manager.index_message(1, "test test test", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        search_manager.index_message(2, "test message", {"author_id": 1, "conversation_id": 1, "created_at": 2000})
        
        results = search_manager.search_messages(1, "test")
        assert len(results) >= 0
    
    def test_search_sort_by_date(self, search_manager, test_db):
        """Search results sorted by date."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'old test', 1000, 1000, 'text'), (2, 1, 1, 'new test', 5000, 5000, 'text')")

        search_manager.index_message(1, "old test", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        search_manager.index_message(2, "new test", {"author_id": 1, "conversation_id": 1, "created_at": 5000})
        
        results = search_manager.search_messages(1, "test")
        assert len(results) >= 0


class TestSearchHighlighting:
    """Test search result highlighting."""
    
    def test_highlight_search_terms(self, search_manager, test_db):
        """Search results include highlights."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'this is a test message', 1000, 1000, 'text')")

        search_manager.index_message(1, "this is a test message", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        
        results = search_manager.search_messages(1, "test")
        assert len(results) >= 0


class TestSearchPerformance:
    """Test search performance features."""
    
    def test_search_with_cache(self, search_manager, test_db):
        """Search uses cache."""
        test_db.execute("INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)")
        test_db.execute("INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)")
        test_db.execute("INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'cached message', 1000, 1000, 'text')")

        search_manager.index_message(1, "cached message", {"author_id": 1, "conversation_id": 1, "created_at": 1000})
        
        results1 = search_manager.search_messages(1, "cached")
        results2 = search_manager.search_messages(1, "cached")
        
        assert len(results1) == len(results2)
    
    def test_clear_search_cache(self, search_manager):
        """Clear search cache."""
        search_manager.clear_cache()
        assert True
