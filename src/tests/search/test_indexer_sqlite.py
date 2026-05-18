"""
Tests for SQLite FTS5 indexer.
"""

import pytest
import uuid

from src.core.search.indexer.sqlite_fts import SQLiteFTS5Indexer
from src.core.search.models import IndexedMessage, IndexedUser, IndexedServer


@pytest.mark.search
class TestSQLiteFTS5Initialization:
    """Test SQLite FTS5 indexer initialization."""

    def test_initialize(self, db_and_modules):
        """Test indexer initialization."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        result = indexer.initialize()

        assert result is True

    def test_double_initialize(self, db_and_modules):
        """Test double initialization is safe."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()
        result = indexer.initialize()

        assert result is True

    def test_health_check(self, db_and_modules):
        """Test health check."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        assert indexer.health_check() is True


@pytest.mark.search
class TestSQLiteFTS5MessageIndexing:
    """Test message indexing with SQLite FTS5."""

    def test_index_message(self, db_and_modules):
        """Test indexing a single message."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]

        message = IndexedMessage(
            message_id=int(unique_id[:6], 16),
            content=f"test message content {unique_id}",
            author_id=1,
            conversation_id=1,
            created_at=1699999999000,
        )

        result = indexer.index_message(message)

        assert result is True

    def test_index_messages_batch(self, db_and_modules):
        """Test batch indexing messages."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]

        messages = [
            IndexedMessage(
                message_id=int(f"{i}{unique_id[:4]}", 16) % 10000000 + i,
                content=f"batch message {i} {unique_id}",
                author_id=1,
                conversation_id=1,
            )
            for i in range(5)
        ]

        count = indexer.index_messages_batch(messages)

        assert count == 5

    def test_remove_message(self, db_and_modules):
        """Test removing a message from index."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]
        msg_id = int(unique_id[:6], 16) % 10000000

        message = IndexedMessage(
            message_id=msg_id,
            content=f"removable message {unique_id}",
            author_id=1,
            conversation_id=1,
        )

        indexer.index_message(message)
        result = indexer.remove_message(msg_id)

        assert result is True

    def test_update_message(self, db_and_modules):
        """Test updating an indexed message."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]
        msg_id = int(unique_id[:6], 16) % 10000000

        message = IndexedMessage(
            message_id=msg_id,
            content=f"original content {unique_id}",
            author_id=1,
            conversation_id=1,
        )
        indexer.index_message(message)

        message.content = f"updated content {unique_id}"
        result = indexer.update_message(message)

        assert result is True


@pytest.mark.search
class TestSQLiteFTS5MessageSearch:
    """Test message search with SQLite FTS5."""

    def test_search_messages_basic(self, db_and_modules):
        """Test basic message search."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]

        message = IndexedMessage(
            message_id=int(unique_id[:6], 16) % 10000000,
            content=f"searchable content {unique_id}",
            author_id=1,
            conversation_id=100,
        )
        indexer.index_message(message)

        results = indexer.search_messages(unique_id, conversation_ids=[100])

        assert len(results) >= 1

    def test_search_messages_empty_query(self, db_and_modules):
        """Test search with empty query."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        results = indexer.search_messages("")

        assert results == []

    def test_search_messages_with_filters(self, db_and_modules):
        """Test search with conversation filter."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]
        conv_id = int(unique_id[:4], 16) % 10000

        message = IndexedMessage(
            message_id=int(unique_id[:6], 16) % 10000000,
            content=f"filtered content {unique_id}",
            author_id=1,
            conversation_id=conv_id,
        )
        indexer.index_message(message)

        results = indexer.search_messages(unique_id, conversation_ids=[conv_id])

        for result in results:
            assert result.conversation_id == conv_id

    def test_search_messages_limit_offset(self, db_and_modules):
        """Test search with limit and offset."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        results = indexer.search_messages("test", limit=5, offset=0)

        assert len(results) <= 5


@pytest.mark.search
class TestSQLiteFTS5UserIndexing:
    """Test user indexing with SQLite FTS5."""

    def test_index_user(self, db_and_modules):
        """Test indexing a user."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]

        user = IndexedUser(
            user_id=int(unique_id[:6], 16) % 10000000,
            username=f"testuser_{unique_id}",
            display_name="Test User",
        )

        result = indexer.index_user(user)

        assert result is True

    def test_search_users(self, db_and_modules):
        """Test searching users."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]

        user = IndexedUser(
            user_id=int(unique_id[:6], 16) % 10000000,
            username=f"searchuser_{unique_id}",
            display_name="Searchable User",
        )
        indexer.index_user(user)

        results = indexer.search_users(unique_id)

        assert len(results) >= 1

    def test_remove_user(self, db_and_modules):
        """Test removing a user from index."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]
        user_id = int(unique_id[:6], 16) % 10000000

        user = IndexedUser(
            user_id=user_id,
            username=f"removeuser_{unique_id}",
        )
        indexer.index_user(user)

        result = indexer.remove_user(user_id)

        assert result is True


@pytest.mark.search
class TestSQLiteFTS5ServerIndexing:
    """Test server indexing with SQLite FTS5."""

    def test_index_server(self, db_and_modules):
        """Test indexing a server."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]

        server = IndexedServer(
            server_id=int(unique_id[:6], 16) % 10000000,
            name=f"Test Server {unique_id}",
            description="A test server",
            tags=["gaming", "fun"],
            is_public=True,
        )

        result = indexer.index_server(server)

        assert result is True

    def test_search_servers(self, db_and_modules):
        """Test searching servers."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]

        server = IndexedServer(
            server_id=int(unique_id[:6], 16) % 10000000,
            name=f"Searchable Server {unique_id}",
            description="A searchable server",
            is_public=True,
        )
        indexer.index_server(server)

        results = indexer.search_servers(unique_id, public_only=True)

        assert len(results) >= 1

    def test_search_servers_by_category(self, db_and_modules):
        """Test searching servers by category."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        unique_id = uuid.uuid4().hex[:8]

        server = IndexedServer(
            server_id=int(unique_id[:6], 16) % 10000000,
            name=f"Gaming Server {unique_id}",
            category="gaming",
            is_public=True,
        )
        indexer.index_server(server)

        results = indexer.search_servers(unique_id, category="gaming")

        for result in results:
            assert result.category == "gaming"


@pytest.mark.search
class TestSQLiteFTS5Stats:
    """Test indexer statistics."""

    def test_get_stats(self, db_and_modules):
        """Test getting indexer stats."""
        db, auth, messaging, servers, search = db_and_modules

        indexer = SQLiteFTS5Indexer(db)
        indexer.initialize()

        stats = indexer.get_stats()

        assert stats["backend"] == "sqlite_fts5"
        assert "message_count" in stats
        assert "user_count" in stats
        assert "server_count" in stats
        assert stats["healthy"] is True
