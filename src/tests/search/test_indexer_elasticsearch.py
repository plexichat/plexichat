"""
Tests for Elasticsearch indexer (with mocked HTTP).
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
    logger.setup(log_dir="temp_es_test/logs", level="WARNING", zip_logs=False)
except Exception:
    pass

from src.core.search.indexer.elasticsearch import ElasticsearchIndexer
from src.core.search.indexer.base import IndexerConfig
from src.core.search.models import IndexedMessage, IndexedUser, IndexedServer
from src.core.search.exceptions import SearchBackendError


class MockHttpClient:
    """Mock HTTP client for Elasticsearch tests."""
    
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
        
        if method == "PUT" and "/_doc/" in path:
            return {"result": "created"}
        if method == "DELETE" and "/_doc/" in path:
            return {"result": "deleted"}
        if method == "POST" and "/_search" in path:
            return {"hits": {"hits": []}}
        if method == "GET" and "/_count" in path:
            return {"count": 0}
        if method == "PUT" and path.startswith("/"):
            return {"acknowledged": True}
        
        return {}


@pytest.mark.search
class TestElasticsearchInitialization:
    """Test Elasticsearch indexer initialization."""
    
    def test_initialize_with_mock(self):
        """Test initialization with mock client."""
        mock_client = MockHttpClient()
        
        indexer = ElasticsearchIndexer(
            hosts=["http://localhost:9200"],
            http_client=mock_client
        )
        
        result = indexer.initialize()
        
        assert result is True
        assert indexer._initialized is True
    
    def test_double_initialize(self):
        """Test double initialization is safe."""
        mock_client = MockHttpClient()
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
        indexer.initialize()
        result = indexer.initialize()
        
        assert result is True


@pytest.mark.search
class TestElasticsearchMessageIndexing:
    """Test message indexing with Elasticsearch."""
    
    def test_index_message(self):
        """Test indexing a single message."""
        mock_client = MockHttpClient()
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        message = IndexedMessage(
            message_id=123,
            content="test message",
            author_id=1,
            conversation_id=1,
        )
        
        result = indexer.index_message(message)
        
        assert result is True
        assert any("/_doc/123" in req[1] for req in mock_client.requests)
    
    def test_remove_message(self):
        """Test removing a message."""
        mock_client = MockHttpClient()
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        result = indexer.remove_message(123)
        
        assert result is True
    
    def test_search_messages(self):
        """Test searching messages."""
        mock_client = MockHttpClient()
        mock_client.set_response("POST", "/plexichat_messages/_search", {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "message_id": "123",
                            "content": "test message",
                            "author_id": "1",
                            "conversation_id": "1",
                            "created_at": 1699999999000,
                        },
                        "_score": 1.5
                    }
                ]
            }
        })
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        results = indexer.search_messages("test")
        
        assert len(results) == 1
        assert results[0].message_id == 123
        assert results[0].content == "test message"


@pytest.mark.search
class TestElasticsearchUserIndexing:
    """Test user indexing with Elasticsearch."""
    
    def test_index_user(self):
        """Test indexing a user."""
        mock_client = MockHttpClient()
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
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
        mock_client.set_response("POST", "/plexichat_users/_search", {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "user_id": "1",
                            "username": "alice",
                            "display_name": "Alice",
                            "is_bot": False
                        },
                        "_score": 2.0
                    }
                ]
            }
        })
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        results = indexer.search_users("alice")
        
        assert len(results) == 1
        assert results[0].username == "alice"


@pytest.mark.search
class TestElasticsearchServerIndexing:
    """Test server indexing with Elasticsearch."""
    
    def test_index_server(self):
        """Test indexing a server."""
        mock_client = MockHttpClient()
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
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
        mock_client.set_response("POST", "/plexichat_servers/_search", {
            "hits": {
                "hits": [
                    {
                        "_source": {
                            "server_id": "1",
                            "name": "Gaming Server",
                            "description": "A gaming community",
                            "tags": ["gaming", "fun"],
                            "category": "gaming",
                            "member_count": 100,
                            "is_public": True
                        },
                        "_score": 3.0
                    }
                ]
            }
        })
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        results = indexer.search_servers("gaming")
        
        assert len(results) == 1
        assert results[0].name == "Gaming Server"


@pytest.mark.search
class TestElasticsearchStats:
    """Test Elasticsearch statistics."""
    
    def test_get_stats(self):
        """Test getting stats."""
        mock_client = MockHttpClient()
        mock_client.set_response("GET", "/plexichat_messages/_count", {"count": 100})
        mock_client.set_response("GET", "/plexichat_users/_count", {"count": 50})
        mock_client.set_response("GET", "/plexichat_servers/_count", {"count": 10})
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
        indexer.initialize()
        
        stats = indexer.get_stats()
        
        assert stats["backend"] == "elasticsearch"
        assert stats["message_count"] == 100
        assert stats["user_count"] == 50
        assert stats["server_count"] == 10
        assert stats["healthy"] is True


@pytest.mark.search
class TestElasticsearchBatchIndexing:
    """Test batch indexing with Elasticsearch."""
    
    def test_index_messages_batch(self):
        """Test batch indexing messages."""
        mock_client = MockHttpClient()
        
        indexer = ElasticsearchIndexer(http_client=mock_client)
        indexer._initialized = True
        
        messages = [
            IndexedMessage(message_id=i, content=f"message {i}", author_id=1, conversation_id=1)
            for i in range(5)
        ]
        
        with patch.object(indexer, '_request') as mock_request:
            mock_request.return_value = {"errors": False, "items": []}
            
            with patch('urllib.request.urlopen') as mock_urlopen:
                mock_response = Mock()
                mock_response.read.return_value = json.dumps({"errors": False, "items": []}).encode()
                mock_response.__enter__ = Mock(return_value=mock_response)
                mock_response.__exit__ = Mock(return_value=False)
                mock_urlopen.return_value = mock_response
                
                count = indexer.index_messages_batch(messages)
        
        assert count == 5
