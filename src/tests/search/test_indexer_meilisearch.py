"""
Tests for Meilisearch indexer client code.

Uses a mock HTTP client that returns responses matching Meilisearch's actual API.
This tests that our client code correctly:
- Builds request bodies
- Parses responses
- Handles the Meilisearch API contract
"""

import pytest

# common-utils is now a native package.

from src.core.search.indexer.meilisearch import MeilisearchIndexer  # noqa: E402
from src.core.search.models import IndexedMessage  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def setup_logger(tmp_path_factory):
    """Setup logger for meilisearch tests."""
    import utils.logger as logger

    log_dir = str(tmp_path_factory.mktemp("ms_test_logs"))
    try:
        logger.setup(log_dir=log_dir, level="WARNING", zip_logs=False)
    except Exception:
        pass
    yield


class MeilisearchMock:
    """
    Mock that returns responses matching Meilisearch's actual API format.
    See: https://www.meilisearch.com/docs/reference/api/overview
    """

    def __init__(self):
        self.requests = []
        self.indexed_docs = {"messages": {}, "users": {}, "servers": {}}
        self._task_id = 0

    def request(self, method, path, body=None):
        self.requests.append({"method": method, "path": path, "body": body})

        if method == "POST" and path == "/indexes":
            self._task_id += 1
            return {"taskUid": self._task_id, "status": "enqueued"}

        if method == "PATCH" and "/settings" in path:
            self._task_id += 1
            return {"taskUid": self._task_id, "status": "enqueued"}

        if method == "POST" and "/documents" in path:
            index = path.split("/")[2]
            index_type = self._get_index_type(index)
            if isinstance(body, list):
                for doc in body:
                    pk = self._get_primary_key(index_type, doc)
                    self.indexed_docs[index_type][pk] = doc
            self._task_id += 1
            return {"taskUid": self._task_id, "status": "enqueued"}

        if method == "DELETE" and "/documents/" in path:
            self._task_id += 1
            return {"taskUid": self._task_id, "status": "enqueued"}

        if method == "POST" and "/search" in path:
            index = path.split("/")[2]
            return self._search(index, body)

        if method == "GET" and "/stats" in path:
            index = path.split("/")[2]
            index_type = self._get_index_type(index)
            return {
                "numberOfDocuments": len(self.indexed_docs[index_type]),
                "isIndexing": False,
                "fieldDistribution": {},
            }

        return {}

    def _get_index_type(self, index):
        if "messages" in index:
            return "messages"
        if "users" in index:
            return "users"
        return "servers"

    def _get_primary_key(self, index_type, doc):
        if index_type == "messages":
            return doc.get("message_id", "")
        if index_type == "users":
            return doc.get("user_id", "")
        return doc.get("server_id", "")

    def _search(self, index, body):
        index_type = self._get_index_type(index)
        docs = self.indexed_docs[index_type]
        query = body.get("q", "") if body else ""
        filter_str = body.get("filter", "") if body else ""

        hits = []
        for doc_id, doc in docs.items():
            searchable = " ".join(str(v) for v in doc.values() if isinstance(v, str))
            if query.lower() in searchable.lower():
                if self._matches_filter(doc, filter_str):
                    hits.append(doc)

        limit = body.get("limit", 20) if body else 20
        offset = body.get("offset", 0) if body else 0

        return {
            "hits": hits[offset : offset + limit],
            "offset": offset,
            "limit": limit,
            "estimatedTotalHits": len(hits),
            "processingTimeMs": 1,
            "query": query,
        }

    def _matches_filter(self, doc, filter_str):
        if not filter_str:
            return True
        if "is_public = true" in filter_str and not doc.get("is_public"):
            return False
        return True


@pytest.mark.search
class TestMeilisearchClientRequests:
    """Test that client builds correct request bodies."""

    def test_index_message_request_format(self):
        """Test message indexing builds correct Meilisearch document."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        message = IndexedMessage(
            message_id=12345,
            content="Hello world",
            author_id=100,
            conversation_id=200,
            server_id=300,
            channel_id=400,
            created_at=1699999999000,
            has_attachments=True,
            mentions=[500, 600],
        )
        indexer.index_message(message)

        doc_request = next(
            r
            for r in mock.requests
            if "/documents" in r["path"] and r["method"] == "POST"
        )
        doc = doc_request["body"][0]

        assert doc["message_id"] == "12345"
        assert doc["content"] == "Hello world"
        assert doc["author_id"] == "100"
        assert doc["conversation_id"] == "200"
        assert doc["has_attachments"] is True
        assert doc["mentions"] == ["500", "600"]

    def test_search_request_with_filters(self):
        """Test search builds correct filter string."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        indexer.search_messages(
            "hello",
            conversation_ids=[1, 2],
            server_ids=[10],
        )

        search_request = next(r for r in mock.requests if "/search" in r["path"])
        body = search_request["body"]

        assert body["q"] == "hello"
        assert "filter" in body
        assert "conversation_id" in body["filter"]
        assert "server_id" in body["filter"]

    def test_index_settings_configured(self):
        """Test that index settings are configured on init."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        settings_requests = [r for r in mock.requests if "/settings" in r["path"]]

        assert len(settings_requests) >= 3

        msg_settings = next(r for r in settings_requests if "messages" in r["path"])
        assert "searchableAttributes" in msg_settings["body"]
        assert "filterableAttributes" in msg_settings["body"]


@pytest.mark.search
class TestMeilisearchResponseParsing:
    """Test that client correctly parses Meilisearch responses."""

    def test_parse_search_results(self):
        """Test parsing search response into result objects."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        mock.indexed_docs["messages"]["123"] = {
            "message_id": "123",
            "content": "test message",
            "author_id": "1",
            "conversation_id": "10",
            "server_id": "100",
            "channel_id": "1000",
            "created_at": 1699999999000,
            "has_attachments": False,
            "is_pinned": True,
        }

        results = indexer.search_messages("test")

        assert len(results) == 1
        assert results[0].message_id == 123
        assert results[0].content == "test message"
        assert results[0].author_id == 1
        assert results[0].is_pinned is True

    def test_parse_user_search_results(self):
        """Test parsing user search response."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        mock.indexed_docs["users"]["456"] = {
            "user_id": "456",
            "username": "alice",
            "display_name": "Alice Smith",
            "is_bot": False,
        }

        results = indexer.search_users("alice")

        assert len(results) == 1
        assert results[0].user_id == 456
        assert results[0].username == "alice"
        assert results[0].display_name == "Alice Smith"

    def test_parse_server_search_results(self):
        """Test parsing server search response."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        mock.indexed_docs["servers"]["789"] = {
            "server_id": "789",
            "name": "Gaming Hub",
            "description": "A gaming community",
            "tags": ["gaming", "fun"],
            "category": "gaming",
            "member_count": 500,
            "is_public": True,
        }

        results = indexer.search_servers("gaming")

        assert len(results) == 1
        assert results[0].server_id == 789
        assert results[0].name == "Gaming Hub"
        assert results[0].tags == ["gaming", "fun"]


@pytest.mark.search
class TestMeilisearchIndexOperations:
    """Test index CRUD operations."""

    def test_index_and_search_roundtrip(self):
        """Test indexing then searching finds the document."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        message = IndexedMessage(
            message_id=999,
            content="unique searchable content xyz123",
            author_id=1,
            conversation_id=1,
        )
        indexer.index_message(message)

        results = indexer.search_messages("xyz123")

        assert len(results) == 1
        assert results[0].message_id == 999

    def test_batch_index(self):
        """Test batch indexing multiple documents."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        messages = [
            IndexedMessage(
                message_id=i, content=f"message {i}", author_id=1, conversation_id=1
            )
            for i in range(5)
        ]

        count = indexer.index_messages_batch(messages)

        assert count == 5
        assert len(mock.indexed_docs["messages"]) == 5

    def test_remove_message(self):
        """Test removing a message sends DELETE request."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        indexer.remove_message(12345)

        delete_request = next(
            (
                r
                for r in mock.requests
                if r["method"] == "DELETE" and "/documents/" in r["path"]
            ),
            None,
        )
        assert delete_request is not None
        assert "12345" in delete_request["path"]

    def test_get_stats(self):
        """Test stats returns document counts."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        mock.indexed_docs["messages"] = {"1": {}, "2": {}, "3": {}}
        mock.indexed_docs["users"] = {"1": {}}
        mock.indexed_docs["servers"] = {"1": {}, "2": {}}

        stats = indexer.get_stats()

        assert stats["backend"] == "meilisearch"
        assert stats["message_count"] == 3
        assert stats["user_count"] == 1
        assert stats["server_count"] == 2
        assert stats["healthy"] is True


@pytest.mark.search
class TestMeilisearchPublicServerFilter:
    """Test that public_only filter works correctly."""

    def test_search_servers_public_only(self):
        """Test that public_only=True filters out private servers."""
        mock = MeilisearchMock()
        indexer = MeilisearchIndexer(http_client=mock)
        indexer.initialize()

        mock.indexed_docs["servers"]["1"] = {
            "server_id": "1",
            "name": "Public Server",
            "is_public": True,
        }
        mock.indexed_docs["servers"]["2"] = {
            "server_id": "2",
            "name": "Private Server",
            "is_public": False,
        }

        results = indexer.search_servers("Server", public_only=True)

        assert len(results) == 1
        assert results[0].name == "Public Server"
