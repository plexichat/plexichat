"""
Tests for Meilisearch indexer (with mocked HTTP).
"""

import pytest
import json
import os
import sys
from unittest.mock import Mock, patch

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

import utils.logger as logger

try:
    logger.setup(log_dir="temp_ms_test/logs", level="WARNING", zip_logs=False)
except Exception:
    pass

from src.core.search.indexer.meilisearch import MeilisearchIndexer
from src.core.search.indexer.base import IndexerConfig
from src.core.search.models import IndexedMessage, IndexedUser, IndexedServer
from src.core.search.exceptions import SearchBackendError


class MockHttpClient:
    """Mock HTTP client for Meilisearch tests."""
    
    def __init__(self):
        self.requests = []
        self.responses = {}
    
    def set_response(self, method, path, response):
        """Set a mock response for a request."""
        self.responses[(method, path)] = response
    
    def request(self, method, path, body=None):
        """Make a mock request."""
        self.requests.append((method, path, body))
        
        key = (method, path)
        if key in self.responses:
            return self.responses[key]
        
        if method == "POST" and "/indexes" in path and "/documents" not in path:
            return {"taskUid": 1}
        if method == "PATCH" and "/settings" in path:
            return {"taskUid": 2}
        if method == "POST" and "/documents" in path:
            return {"taskUid": 3}
        if method == "DELETE" and "/documents/" in path:
            return {"taskUid": 4}
        if method == "POST" and "/search" in path:
            return {"hits": []}
        if method == "GET" and "/stats" in path:
            return {"numberOfDocuments": 0}
        
        return {}


@pytest.mark.search
class TestMeilisearchInitialization:
    """Test Meilisearch indexer initialization."""
    
    def test_initialize_with_mock(self):
        """Test initialization with mock client."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(
            host="http://localhost:7700",
            http_client=mock_client
        )
        
        result = indexer.initialize()
        
        assert result is True
        assert indexer._initialized is True
    
    def test_double_initialize(self):
        """Test double initialization is safe."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        result = indexer.initialize()
        
        assert result is True
    
    def test_initialize_with_api_key(self):
        """Test initialization with API key."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(
            host="http://localhost:7700",
            api_key="test-api-key",
            http_client=mock_client
        )
        
        result = indexer.initialize()
        
        assert result is True


@pytest.mark.search
class TestMeilisearchMessageIndexing:
    """Test message indexing with Meilisearch."""
    
    def test_index_message(self):
        """Test indexing a single message."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        message = IndexedMessage(
            message_id=123,
            content="test message",
            author_id=1,
            conversation_id=1,
        )
        
        result = indexer.index_message(message)
        
        assert result is True
    
    def test_index_messages_batch(self):
        """Test batch indexing messages."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        messages = [
            IndexedMessage(message_id=i, content=f"message {i}", author_id=1, conversation_id=1)
            for i in range(5)
        ]
        
        count = indexer.index_messages_batch(messages)
        
        assert count == 5
    
    def test_remove_message(self):
        """Test removing a message."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        result = indexer.remove_message(123)
        
        assert result is True
    
    def test_search_messages(self):
        """Test searching messages."""
        mock_client = MockHttpClient()
        mock_client.set_response("POST", "/indexes/plexichat_messages/search", {
            "hits": [
                {
                    "message_id": "123",
                    "content": "test message",
                    "author_id": "1",
                    "conversation_id": "1",
                    "created_at": 1699999999000,
                }
            ]
        })
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        results = indexer.search_messages("test")
        
        assert len(results) == 1
        assert results[0].message_id == 123
        assert results[0].content == "test message"
    
    def test_search_messages_with_filters(self):
        """Test searching messages with filters."""
        mock_client = MockHttpClient()
        mock_client.set_response("POST", "/indexes/plexichat_messages/search", {
            "hits": []
        })
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        results = indexer.search_messages(
            "test",
            conversation_ids=[1, 2],
            server_ids=[10],
            channel_ids=[100]
        )
        
        assert isinstance(results, list)
        
        search_request = next(
            (r for r in mock_client.requests if r[0] == "POST" and "/search" in r[1]),
            None
        )
        assert search_request is not None
        assert "filter" in search_request[2]


@pytest.mark.search
class TestMeilisearchUserIndexing:
    """Test user indexing with Meilisearch."""
    
    def test_index_user(self):
        """Test indexing a user."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        user = IndexedUser(
            user_id=1,
            username="testuser",
            display_name="Test User"
        )
        
        result = indexer.index_user(user)
        
        assert result is True
    
    def test_search_users(self):
        """Test searching users."""
        mock_client = MockHttpClient()
        mock_client.set_response("POST", "/indexes/plexichat_users/search", {
            "hits": [
                {
                    "user_id": "1",
                    "username": "alice",
                    "display_name": "Alice",
                    "is_bot": False
                }
            ]
        })
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        results = indexer.search_users("alice")
        
        assert len(results) == 1
        assert results[0].username == "alice"
    
    def test_remove_user(self):
        """Test removing a user."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        result = indexer.remove_user(1)
        
        assert result is True


@pytest.mark.search
class TestMeilisearchServerIndexing:
    """Test server indexing with Meilisearch."""
    
    def test_index_server(self):
        """Test indexing a server."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        server = IndexedServer(
            server_id=1,
            name="Test Server",
            description="A test server",
            tags=["gaming"],
            is_public=True
        )
        
        result = indexer.index_server(server)
        
        assert result is True
    
    def test_search_servers(self):
        """Test searching servers."""
        mock_client = MockHttpClient()
        mock_client.set_response("POST", "/indexes/plexichat_servers/search", {
            "hits": [
                {
                    "server_id": "1",
                    "name": "Gaming Server",
                    "description": "A gaming community",
                    "tags": ["gaming", "fun"],
                    "category": "gaming",
                    "member_count": 100,
                    "is_public": True
                }
            ]
        })
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        results = indexer.search_servers("gaming")
        
        assert len(results) == 1
        assert results[0].name == "Gaming Server"
    
    def test_search_servers_by_category(self):
        """Test searching servers by category."""
        mock_client = MockHttpClient()
        mock_client.set_response("POST", "/indexes/plexichat_servers/search", {
            "hits": []
        })
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        results = indexer.search_servers("gaming", category="gaming")
        
        search_request = next(
            (r for r in mock_client.requests if r[0] == "POST" and "/search" in r[1]),
            None
        )
        assert search_request is not None
        assert "filter" in search_request[2]
    
    def test_remove_server(self):
        """Test removing a server."""
        mock_client = MockHttpClient()
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        result = indexer.remove_server(1)
        
        assert result is True


@pytest.mark.search
class TestMeilisearchStats:
    """Test Meilisearch statistics."""
    
    def test_get_stats(self):
        """Test getting stats."""
        mock_client = MockHttpClient()
        mock_client.set_response("GET", "/indexes/plexichat_messages/stats", {"numberOfDocuments": 100})
        mock_client.set_response("GET", "/indexes/plexichat_users/stats", {"numberOfDocuments": 50})
        mock_client.set_response("GET", "/indexes/plexichat_servers/stats", {"numberOfDocuments": 10})
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        stats = indexer.get_stats()
        
        assert stats["backend"] == "meilisearch"
        assert stats["message_count"] == 100
        assert stats["user_count"] == 50
        assert stats["server_count"] == 10
        assert stats["healthy"] is True
    
    def test_health_check(self):
        """Test health check."""
        mock_client = MockHttpClient()
        mock_client.set_response("GET", "/indexes/plexichat_messages/stats", {"numberOfDocuments": 0})
        mock_client.set_response("GET", "/indexes/plexichat_users/stats", {"numberOfDocuments": 0})
        mock_client.set_response("GET", "/indexes/plexichat_servers/stats", {"numberOfDocuments": 0})
        
        indexer = MeilisearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        assert indexer.health_check() is True
