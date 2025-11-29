"""
Elasticsearch indexer - Full-text search using Elasticsearch.

Optional backend for production deployments requiring advanced search features.
"""

import json
from typing import List, Dict, Any, Optional

import utils.logger as logger

from .base import BaseIndexer, IndexerConfig
from ..models import (
    IndexedMessage,
    IndexedUser,
    IndexedServer,
    MessageSearchResult,
    UserSearchResult,
    ServerSearchResult,
)
from ..exceptions import SearchIndexError, SearchBackendError


class ElasticsearchIndexer(BaseIndexer):
    """Elasticsearch full-text search indexer."""
    
    def __init__(
        self,
        hosts: Optional[List[str]] = None,
        index_prefix: str = "plexichat",
        config: Optional[IndexerConfig] = None,
        http_client=None,
    ):
        super().__init__(config)
        self._hosts = hosts or ["http://localhost:9200"]
        self._index_prefix = index_prefix
        self._http_client = http_client
        self._initialized = False
        
        self._message_index = f"{index_prefix}_messages"
        self._user_index = f"{index_prefix}_users"
        self._server_index = f"{index_prefix}_servers"
    
    def initialize(self) -> bool:
        """Initialize Elasticsearch indices."""
        if self._initialized:
            return True
        
        try:
            self._create_message_index()
            self._create_user_index()
            self._create_server_index()
            
            self._initialized = True
            logger.info("Elasticsearch indexer initialized")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize Elasticsearch: {e}")
            raise SearchBackendError(
                f"Failed to initialize Elasticsearch: {e}",
                backend="elasticsearch",
                original_error=e
            )
    
    def close(self):
        """Close the indexer."""
        self._initialized = False
    
    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to Elasticsearch."""
        if self._http_client is None:
            try:
                import urllib.request
                import urllib.error
                
                url = f"{self._hosts[0]}{path}"
                data = json.dumps(body).encode() if body else None
                headers = {"Content-Type": "application/json"}
                
                req = urllib.request.Request(
                    url,
                    data=data,
                    headers=headers,
                    method=method
                )
                
                with urllib.request.urlopen(req, timeout=30) as response:
                    return json.loads(response.read().decode())
                    
            except urllib.error.HTTPError as e:
                error_body = e.read().decode() if e.fp else ""
                raise SearchBackendError(
                    f"Elasticsearch request failed: {e.code} {error_body}",
                    backend="elasticsearch",
                    original_error=e
                )
            except Exception as e:
                raise SearchBackendError(
                    f"Elasticsearch request failed: {e}",
                    backend="elasticsearch",
                    original_error=e
                )
        else:
            return self._http_client.request(method, path, body)
    
    def _create_message_index(self):
        """Create message index with mappings."""
        mapping = {
            "mappings": {
                "properties": {
                    "message_id": {"type": "keyword"},
                    "content": {"type": "text", "analyzer": "standard"},
                    "author_id": {"type": "keyword"},
                    "author_username": {"type": "text"},
                    "conversation_id": {"type": "keyword"},
                    "server_id": {"type": "keyword"},
                    "channel_id": {"type": "keyword"},
                    "created_at": {"type": "long"},
                    "has_attachments": {"type": "boolean"},
                    "has_embeds": {"type": "boolean"},
                    "has_links": {"type": "boolean"},
                    "mentions": {"type": "keyword"},
                    "is_pinned": {"type": "boolean"},
                }
            },
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
            }
        }
        
        try:
            self._request("PUT", f"/{self._message_index}", mapping)
        except SearchBackendError as e:
            if "resource_already_exists" not in str(e).lower():
                raise
    
    def _create_user_index(self):
        """Create user index with mappings."""
        mapping = {
            "mappings": {
                "properties": {
                    "user_id": {"type": "keyword"},
                    "username": {"type": "text", "analyzer": "standard"},
                    "display_name": {"type": "text"},
                    "is_bot": {"type": "boolean"},
                }
            },
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
            }
        }
        
        try:
            self._request("PUT", f"/{self._user_index}", mapping)
        except SearchBackendError as e:
            if "resource_already_exists" not in str(e).lower():
                raise
    
    def _create_server_index(self):
        """Create server index with mappings."""
        mapping = {
            "mappings": {
                "properties": {
                    "server_id": {"type": "keyword"},
                    "name": {"type": "text", "analyzer": "standard"},
                    "description": {"type": "text"},
                    "tags": {"type": "keyword"},
                    "category": {"type": "keyword"},
                    "member_count": {"type": "integer"},
                    "is_public": {"type": "boolean"},
                }
            },
            "settings": {
                "number_of_shards": 1,
                "number_of_replicas": 0,
            }
        }
        
        try:
            self._request("PUT", f"/{self._server_index}", mapping)
        except SearchBackendError as e:
            if "resource_already_exists" not in str(e).lower():
                raise
    
    def index_message(self, message: IndexedMessage) -> bool:
        """Index a single message."""
        try:
            doc = {
                "message_id": str(message.message_id),
                "content": message.content or "",
                "author_id": str(message.author_id),
                "author_username": "",
                "conversation_id": str(message.conversation_id),
                "server_id": str(message.server_id) if message.server_id else None,
                "channel_id": str(message.channel_id) if message.channel_id else None,
                "created_at": message.created_at,
                "has_attachments": message.has_attachments,
                "has_embeds": message.has_embeds,
                "has_links": message.has_links,
                "mentions": [str(m) for m in message.mentions] if message.mentions else [],
                "is_pinned": message.is_pinned,
            }
            
            self._request(
                "PUT",
                f"/{self._message_index}/_doc/{message.message_id}",
                doc
            )
            return True
            
        except Exception as e:
            logger.error(f"Failed to index message {message.message_id}: {e}")
            raise SearchIndexError(
                f"Failed to index message: {e}",
                item_id=message.message_id
            )
    
    def index_messages_batch(self, messages: List[IndexedMessage]) -> int:
        """Index multiple messages using bulk API."""
        if not messages:
            return 0
        
        try:
            bulk_body = []
            for message in messages:
                bulk_body.append(json.dumps({
                    "index": {
                        "_index": self._message_index,
                        "_id": str(message.message_id)
                    }
                }))
                bulk_body.append(json.dumps({
                    "message_id": str(message.message_id),
                    "content": message.content or "",
                    "author_id": str(message.author_id),
                    "conversation_id": str(message.conversation_id),
                    "server_id": str(message.server_id) if message.server_id else None,
                    "channel_id": str(message.channel_id) if message.channel_id else None,
                    "created_at": message.created_at,
                    "has_attachments": message.has_attachments,
                    "has_embeds": message.has_embeds,
                    "has_links": message.has_links,
                    "mentions": [str(m) for m in message.mentions] if message.mentions else [],
                    "is_pinned": message.is_pinned,
                }))
            
            bulk_data = "\n".join(bulk_body) + "\n"
            
            import urllib.request
            url = f"{self._hosts[0]}/_bulk"
            req = urllib.request.Request(
                url,
                data=bulk_data.encode(),
                headers={"Content-Type": "application/x-ndjson"},
                method="POST"
            )
            
            with urllib.request.urlopen(req, timeout=60) as response:
                result = json.loads(response.read().decode())
                
            if result.get("errors"):
                failed = sum(1 for item in result.get("items", []) if "error" in item.get("index", {}))
                return len(messages) - failed
            
            return len(messages)
            
        except Exception as e:
            logger.error(f"Bulk indexing failed: {e}")
            indexed = 0
            for message in messages:
                try:
                    if self.index_message(message):
                        indexed += 1
                except SearchIndexError:
                    continue
            return indexed
    
    def remove_message(self, message_id: int) -> bool:
        """Remove a message from the index."""
        try:
            self._request("DELETE", f"/{self._message_index}/_doc/{message_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to remove message {message_id}: {e}")
            return False
    
    def update_message(self, message: IndexedMessage) -> bool:
        """Update an indexed message."""
        return self.index_message(message)
    
    def search_messages(
        self,
        query: str,
        conversation_ids: Optional[List[int]] = None,
        server_ids: Optional[List[int]] = None,
        channel_ids: Optional[List[int]] = None,
        author_ids: Optional[List[int]] = None,
        limit: int = 25,
        offset: int = 0,
    ) -> List[MessageSearchResult]:
        """Search messages using Elasticsearch."""
        try:
            must: List[Dict[str, Any]] = [{"match": {"content": query}}]
            
            if conversation_ids:
                must.append({"terms": {"conversation_id": [str(c) for c in conversation_ids]}})
            if server_ids:
                must.append({"terms": {"server_id": [str(s) for s in server_ids]}})
            if channel_ids:
                must.append({"terms": {"channel_id": [str(c) for c in channel_ids]}})
            if author_ids:
                must.append({"terms": {"author_id": [str(a) for c in author_ids]}})
            
            search_body = {
                "query": {"bool": {"must": must}},
                "from": offset,
                "size": limit,
                "sort": [{"_score": "desc"}, {"created_at": "desc"}],
            }
            
            result = self._request(
                "POST",
                f"/{self._message_index}/_search",
                search_body
            )
            
            results = []
            for hit in result.get("hits", {}).get("hits", []):
                source = hit.get("_source", {})
                results.append(MessageSearchResult(
                    id=int(source.get("message_id", 0)),
                    message_id=int(source.get("message_id", 0)),
                    content=source.get("content", ""),
                    author_id=int(source.get("author_id", 0)),
                    conversation_id=int(source.get("conversation_id", 0)),
                    server_id=int(source["server_id"]) if source.get("server_id") else None,
                    channel_id=int(source["channel_id"]) if source.get("channel_id") else None,
                    created_at=source.get("created_at", 0),
                    has_attachments=source.get("has_attachments", False),
                    is_pinned=source.get("is_pinned", False),
                    score=hit.get("_score", 0.0),
                ))
            
            return results
            
        except Exception as e:
            logger.error(f"Elasticsearch search failed: {e}")
            return []
    
    def index_user(self, user: IndexedUser) -> bool:
        """Index a user."""
        try:
            doc = {
                "user_id": str(user.user_id),
                "username": user.username or "",
                "display_name": user.display_name or "",
                "is_bot": user.is_bot,
            }
            
            self._request("PUT", f"/{self._user_index}/_doc/{user.user_id}", doc)
            return True
            
        except Exception as e:
            logger.error(f"Failed to index user {user.user_id}: {e}")
            return False
    
    def remove_user(self, user_id: int) -> bool:
        """Remove a user from the index."""
        try:
            self._request("DELETE", f"/{self._user_index}/_doc/{user_id}")
            return True
        except Exception:
            return False
    
    def search_users(
        self,
        query: str,
        limit: int = 25,
        offset: int = 0,
    ) -> List[UserSearchResult]:
        """Search users using Elasticsearch."""
        try:
            search_body = {
                "query": {
                    "multi_match": {
                        "query": query,
                        "fields": ["username^2", "display_name"],
                    }
                },
                "from": offset,
                "size": limit,
            }
            
            result = self._request("POST", f"/{self._user_index}/_search", search_body)
            
            results = []
            for hit in result.get("hits", {}).get("hits", []):
                source = hit.get("_source", {})
                results.append(UserSearchResult(
                    id=int(source.get("user_id", 0)),
                    user_id=int(source.get("user_id", 0)),
                    username=source.get("username", ""),
                    display_name=source.get("display_name") or None,
                    is_bot=source.get("is_bot", False),
                    score=hit.get("_score", 0.0),
                ))
            
            return results
            
        except Exception as e:
            logger.error(f"Elasticsearch user search failed: {e}")
            return []
    
    def index_server(self, server: IndexedServer) -> bool:
        """Index a server."""
        try:
            doc = {
                "server_id": str(server.server_id),
                "name": server.name or "",
                "description": server.description or "",
                "tags": server.tags or [],
                "category": server.category or "",
                "member_count": server.member_count,
                "is_public": server.is_public,
            }
            
            self._request("PUT", f"/{self._server_index}/_doc/{server.server_id}", doc)
            return True
            
        except Exception as e:
            logger.error(f"Failed to index server {server.server_id}: {e}")
            return False
    
    def remove_server(self, server_id: int) -> bool:
        """Remove a server from the index."""
        try:
            self._request("DELETE", f"/{self._server_index}/_doc/{server_id}")
            return True
        except Exception:
            return False
    
    def search_servers(
        self,
        query: str,
        category: Optional[str] = None,
        public_only: bool = True,
        limit: int = 25,
        offset: int = 0,
    ) -> List[ServerSearchResult]:
        """Search servers using Elasticsearch."""
        try:
            must = [
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["name^3", "description", "tags"],
                    }
                }
            ]
            
            if public_only:
                must.append({"term": {"is_public": True}})
            if category:
                must.append({"term": {"category": category}})
            
            search_body = {
                "query": {"bool": {"must": must}},
                "from": offset,
                "size": limit,
            }
            
            result = self._request("POST", f"/{self._server_index}/_search", search_body)
            
            results = []
            for hit in result.get("hits", {}).get("hits", []):
                source = hit.get("_source", {})
                results.append(ServerSearchResult(
                    id=int(source.get("server_id", 0)),
                    server_id=int(source.get("server_id", 0)),
                    name=source.get("name", ""),
                    description=source.get("description") or None,
                    category=source.get("category") or None,
                    tags=source.get("tags", []),
                    member_count=source.get("member_count", 0),
                    score=hit.get("_score", 0.0),
                ))
            
            return results
            
        except Exception as e:
            logger.error(f"Elasticsearch server search failed: {e}")
            return []
    
    def get_stats(self) -> Dict[str, Any]:
        """Get indexer statistics."""
        try:
            msg_stats = self._request("GET", f"/{self._message_index}/_count")
            user_stats = self._request("GET", f"/{self._user_index}/_count")
            server_stats = self._request("GET", f"/{self._server_index}/_count")
            
            return {
                "backend": "elasticsearch",
                "message_count": msg_stats.get("count", 0),
                "user_count": user_stats.get("count", 0),
                "server_count": server_stats.get("count", 0),
                "healthy": True,
            }
            
        except Exception as e:
            return {
                "backend": "elasticsearch",
                "healthy": False,
                "error": str(e),
            }
