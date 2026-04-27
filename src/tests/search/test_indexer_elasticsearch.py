"""
Tests for Elasticsearch indexer client code.

Uses a mock HTTP client that returns responses matching Elasticsearch's actual API.
This tests that our client code correctly:
- Builds request bodies
- Parses responses
- Handles the Elasticsearch API contract
"""

import pytest
import os
import sys

project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
src_path = os.path.join(project_root, "src")
common_utils_path = os.path.join(project_root, "src", "utils", "common-utils")

for path in [project_root, src_path, common_utils_path]:
    if path not in sys.path:
        sys.path.insert(0, path)

from src.core.search.indexer.elasticsearch import ElasticsearchIndexer  # noqa: E402
from src.core.search.models import IndexedMessage  # noqa: E402


@pytest.fixture(scope="module", autouse=True)
def setup_logger(tmp_path_factory):
    """Setup logger for elasticsearch tests."""
    import utils.logger as logger

    log_dir = str(tmp_path_factory.mktemp("es_test_logs"))
    try:
        logger.setup(log_dir=log_dir, level="WARNING", zip_logs=False)
    except Exception:
        pass
    yield


class ElasticsearchMock:
    """
    Mock that returns responses matching Elasticsearch's actual API format.
    See: https://www.elastic.co/guide/en/elasticsearch/reference/current/rest-apis.html
    """

    def __init__(self):
        self.requests = []
        self.indexed_docs = {"messages": {}, "users": {}, "servers": {}}

    def request(self, method, path, body=None):
        self.requests.append({"method": method, "path": path, "body": body})

        if method == "PUT" and path.startswith("/plexichat") and "/_doc/" not in path:
            return {
                "acknowledged": True,
                "shards_acknowledged": True,
                "index": path[1:],
            }

        if method == "PUT" and "/_doc/" in path:
            parts = path.split("/")
            index = parts[1]
            doc_id = parts[3]
            index_type = (
                "messages"
                if "messages" in index
                else "users"
                if "users" in index
                else "servers"
            )
            self.indexed_docs[index_type][doc_id] = body
            return {"_index": index, "_id": doc_id, "result": "created", "_version": 1}

        if method == "DELETE" and "/_doc/" in path:
            parts = path.split("/")
            doc_id = parts[3]
            return {
                "_index": parts[1],
                "_id": doc_id,
                "result": "deleted",
                "_version": 1,
            }

        if method == "POST" and "/_search" in path:
            index = path.split("/")[1]
            return self._search(index, body)

        if method == "GET" and "/_count" in path:
            index = path.split("/")[1]
            index_type = (
                "messages"
                if "messages" in index
                else "users"
                if "users" in index
                else "servers"
            )
            return {"count": len(self.indexed_docs[index_type])}

        return {}

    def _search(self, index, body):
        index_type = (
            "messages"
            if "messages" in index
            else "users"
            if "users" in index
            else "servers"
        )
        docs = self.indexed_docs[index_type]

        query_text = ""
        if body and "query" in body:
            query = body["query"]
            if "bool" in query and "must" in query["bool"]:
                for clause in query["bool"]["must"]:
                    if "match" in clause:
                        query_text = list(clause["match"].values())[0]
                    elif "multi_match" in clause:
                        query_text = clause["multi_match"].get("query", "")

        hits = []
        for doc_id, doc in docs.items():
            searchable = " ".join(str(v) for v in doc.values() if isinstance(v, str))
            if query_text.lower() in searchable.lower():
                hits.append(
                    {"_index": index, "_id": doc_id, "_score": 1.0, "_source": doc}
                )

        limit = body.get("size", 25) if body else 25
        offset = body.get("from", 0) if body else 0

        return {
            "took": 5,
            "timed_out": False,
            "_shards": {"total": 1, "successful": 1, "skipped": 0, "failed": 0},
            "hits": {
                "total": {"value": len(hits), "relation": "eq"},
                "max_score": 1.0 if hits else None,
                "hits": hits[offset : offset + limit],
            },
        }


@pytest.mark.search
class TestElasticsearchClientRequests:
    """Test that client builds correct request bodies."""

    def test_index_message_request_format(self):
        """Test message indexing builds correct Elasticsearch document."""
        mock = ElasticsearchMock()
        indexer = ElasticsearchIndexer(http_client=mock)
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

        doc_request = next(r for r in mock.requests if "/_doc/" in r["path"])
        body = doc_request["body"]

        assert body["message_id"] == "12345"
        assert body["content"] == "Hello world"
        assert body["author_id"] == "100"
        assert body["conversation_id"] == "200"
        assert body["server_id"] == "300"
        assert body["channel_id"] == "400"
        assert body["has_attachments"] is True
        assert body["mentions"] == ["500", "600"]

    def test_search_request_with_filters(self):
        """Test search builds correct query with filters."""
        mock = ElasticsearchMock()
        indexer = ElasticsearchIndexer(http_client=mock)
        indexer.initialize()

        indexer.search_messages(
            "hello", conversation_ids=[1, 2], server_ids=[10], author_ids=[100]
        )

        search_request = next(r for r in mock.requests if "/_search" in r["path"])
        query = search_request["body"]["query"]["bool"]["must"]

        has_match = any("match" in clause for clause in query)
        has_conv_filter = any(
            "terms" in clause and "conversation_id" in clause["terms"]
            for clause in query
        )
        has_server_filter = any(
            "terms" in clause and "server_id" in clause["terms"] for clause in query
        )

        assert has_match
        assert has_conv_filter
        assert has_server_filter


@pytest.mark.search
class TestElasticsearchResponseParsing:
    """Test that client correctly parses Elasticsearch responses."""

    def test_parse_search_results(self):
        """Test parsing search response into result objects."""
        mock = ElasticsearchMock()
        indexer = ElasticsearchIndexer(http_client=mock)
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
        assert results[0].conversation_id == 10
        assert results[0].server_id == 100
        assert results[0].is_pinned is True

    def test_parse_user_search_results(self):
        """Test parsing user search response."""
        mock = ElasticsearchMock()
        indexer = ElasticsearchIndexer(http_client=mock)
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
        mock = ElasticsearchMock()
        indexer = ElasticsearchIndexer(http_client=mock)
        indexer.initialize()

        mock.indexed_docs["servers"]["789"] = {
            "server_id": "789",
            "name": "Gaming Hub",
            "description": "A gaming community",
            "tags": ["gaming", "fun"],
            "category": "gaming",
            "member_count": 500,
        }

        results = indexer.search_servers("gaming")

        assert len(results) == 1
        assert results[0].server_id == 789
        assert results[0].name == "Gaming Hub"
        assert results[0].tags == ["gaming", "fun"]
        assert results[0].member_count == 500


@pytest.mark.search
class TestElasticsearchIndexOperations:
    """Test index CRUD operations."""

    def test_index_and_search_roundtrip(self):
        """Test indexing then searching finds the document."""
        mock = ElasticsearchMock()
        indexer = ElasticsearchIndexer(http_client=mock)
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

    def test_remove_message(self):
        """Test removing a message sends DELETE request."""
        mock = ElasticsearchMock()
        indexer = ElasticsearchIndexer(http_client=mock)
        indexer.initialize()

        indexer.remove_message(12345)

        delete_request = next(
            (
                r
                for r in mock.requests
                if r["method"] == "DELETE" and "/_doc/" in r["path"]
            ),
            None,
        )
        assert delete_request is not None
        assert "12345" in delete_request["path"]

    def test_get_stats(self):
        """Test stats returns document counts."""
        mock = ElasticsearchMock()
        indexer = ElasticsearchIndexer(http_client=mock)
        indexer.initialize()

        mock.indexed_docs["messages"] = {"1": {}, "2": {}, "3": {}}
        mock.indexed_docs["users"] = {"1": {}}
        mock.indexed_docs["servers"] = {"1": {}, "2": {}}

        stats = indexer.get_stats()

        assert stats["backend"] == "elasticsearch"
        assert stats["message_count"] == 3
        assert stats["user_count"] == 1
        assert stats["server_count"] == 2
        assert stats["healthy"] is True
