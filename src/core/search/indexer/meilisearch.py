"""
Meilisearch indexer - Full-text search using Meilisearch.

Optional backend for production deployments requiring fast, typo-tolerant search.
"""

import json
import urllib.request
import urllib.error
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Union

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


class MeilisearchIndexer(BaseIndexer):
    """Meilisearch full-text search indexer."""

    def __init__(
        self,
        host: str = "http://localhost:7700",
        api_key: Optional[str] = None,
        index_prefix: str = "plexichat",
        config: Optional[IndexerConfig] = None,
        http_client=None,
    ):
        super().__init__(config)
        # SECURITY: the host string is configured at module-load
        # time. We previously passed it straight into urllib with a
        # blanket ``# nosec B310`` suppression. The configured host
        # could be ``http://10.0.0.5`` (SSRF landing page inside the
        # data-centre network) or ``http://localhost:7700`` (SSRF
        # loopback into a dev instance). Validate the configured
        # host up-front via the centralised URLValidator so the
        # ``ActivityIndexer`` cannot be coerced into hitting an
        # arbitrary internal endpoint at index time.
        try:
            from src.utils.security import URLValidator

            URLValidator().validate_url_for_request(host)
        except Exception as _meili_host_exc:
            import utils.logger as _meili_logger

            _meili_logger.critical(
                "Meilisearch host %r failed SSRF validation; "
                "aborting indexer construction: %s",
                host,
                _meili_host_exc,
            )
            raise SearchBackendError(
                f"Meilisearch host failed SSRF validation: {_meili_host_exc}",
                backend="meilisearch",
                original_error=_meili_host_exc,
            )
        self._host = host.rstrip("/")
        self._api_key = api_key
        self._index_prefix = index_prefix
        self._http_client = http_client
        self._initialized = False

        self._message_index = f"{index_prefix}_messages"
        self._user_index = f"{index_prefix}_users"
        self._server_index = f"{index_prefix}_servers"

    def initialize(self) -> bool:
        """Initialize Meilisearch indices."""
        if self._initialized:
            return True

        try:
            self._create_message_index()
            self._create_user_index()
            self._create_server_index()

            self._initialized = True
            logger.info("Meilisearch indexer initialized")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize Meilisearch: {e}")
            raise SearchBackendError(
                f"Failed to initialize Meilisearch: {e}",
                backend="meilisearch",
                original_error=e,
            )

    @staticmethod
    def _validate_http_url(url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise SearchBackendError(
                f"Invalid Meilisearch URL: {url}",
                backend="meilisearch",
            )
        return url

    def close(self):
        """Close the indexer."""
        self._initialized = False

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[Union[Dict, List]] = None,
    ) -> Dict[str, Any]:
        """Make HTTP request to Meilisearch."""
        if self._http_client is not None:
            return self._http_client.request(method, path, body)
        # nosec B310
        try:
            url = self._validate_http_url(f"{self._host}{path}")
            data = json.dumps(body).encode() if body else None
            headers = {"Content-Type": "application/json"}

            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"

            req = urllib.request.Request(url, data=data, headers=headers, method=method)

            with urllib.request.urlopen(req, timeout=30) as response:  # nosec: B310
                response_data = response.read().decode()
                if response_data:
                    return json.loads(response_data)
                return {}

        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.fp else ""
            raise SearchBackendError(
                f"Meilisearch request failed: {e.code} {error_body}",
                backend="meilisearch",
                original_error=e,
            )
        except Exception as e:
            raise SearchBackendError(
                f"Meilisearch request failed: {e}",
                backend="meilisearch",
                original_error=e,
            )

    def _create_message_index(self):
        """Create message index with settings."""
        try:
            self._request(
                "POST",
                "/indexes",
                {"uid": self._message_index, "primaryKey": "message_id"},
            )
        except SearchBackendError as e:
            if "already exists" not in str(e).lower():
                raise

        self._request(
            "PATCH",
            f"/indexes/{self._message_index}/settings",
            {
                "searchableAttributes": ["content", "author_username"],
                "filterableAttributes": [
                    "conversation_id",
                    "server_id",
                    "channel_id",
                    "author_id",
                    "has_attachments",
                    "is_pinned",
                ],
                "sortableAttributes": ["created_at"],
            },
        )

    def _create_user_index(self):
        """Create user index with settings."""
        try:
            self._request(
                "POST", "/indexes", {"uid": self._user_index, "primaryKey": "user_id"}
            )
        except SearchBackendError as e:
            if "already exists" not in str(e).lower():
                raise

        self._request(
            "PATCH",
            f"/indexes/{self._user_index}/settings",
            {
                "searchableAttributes": ["username", "display_name"],
                "filterableAttributes": ["is_bot"],
            },
        )

    def _create_server_index(self):
        """Create server index with settings."""
        try:
            self._request(
                "POST",
                "/indexes",
                {"uid": self._server_index, "primaryKey": "server_id"},
            )
        except SearchBackendError as e:
            if "already exists" not in str(e).lower():
                raise

        self._request(
            "PATCH",
            f"/indexes/{self._server_index}/settings",
            {
                "searchableAttributes": ["name", "description", "tags"],
                "filterableAttributes": ["category", "is_public"],
                "sortableAttributes": ["member_count"],
            },
        )

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
                "mentions": [str(m) for m in message.mentions]
                if message.mentions
                else [],
                "is_pinned": message.is_pinned,
            }

            self._request("POST", f"/indexes/{self._message_index}/documents", [doc])
            return True

        except Exception as e:
            logger.error(f"Failed to index message {message.message_id}: {e}")
            raise SearchIndexError(
                f"Failed to index message: {e}", item_id=message.message_id
            )

    def index_messages_batch(self, messages: List[IndexedMessage]) -> int:
        """Index multiple messages in batch."""
        if not messages:
            return 0

        try:
            docs = []
            for message in messages:
                docs.append(
                    {
                        "message_id": str(message.message_id),
                        "content": message.content or "",
                        "author_id": str(message.author_id),
                        "conversation_id": str(message.conversation_id),
                        "server_id": str(message.server_id)
                        if message.server_id
                        else None,
                        "channel_id": str(message.channel_id)
                        if message.channel_id
                        else None,
                        "created_at": message.created_at,
                        "has_attachments": message.has_attachments,
                        "has_embeds": message.has_embeds,
                        "has_links": message.has_links,
                        "mentions": [str(m) for m in message.mentions]
                        if message.mentions
                        else [],
                        "is_pinned": message.is_pinned,
                    }
                )

            self._request("POST", f"/indexes/{self._message_index}/documents", docs)
            return len(messages)

        except Exception as e:
            logger.error(f"Batch indexing failed: {e}")
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
            self._request(
                "DELETE", f"/indexes/{self._message_index}/documents/{message_id}"
            )
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
        """Search messages using Meilisearch."""
        try:
            filters = []

            if conversation_ids:
                conv_filter = " OR ".join(
                    f'conversation_id = "{c}"' for c in conversation_ids
                )
                filters.append(f"({conv_filter})")

            if server_ids:
                server_filter = " OR ".join(f'server_id = "{s}"' for s in server_ids)
                filters.append(f"({server_filter})")

            if channel_ids:
                channel_filter = " OR ".join(f'channel_id = "{c}"' for c in channel_ids)
                filters.append(f"({channel_filter})")

            if author_ids:
                author_filter = " OR ".join(f'author_id = "{a}"' for a in author_ids)
                filters.append(f"({author_filter})")

            search_body = {
                "q": query,
                "limit": limit,
                "offset": offset,
                "sort": ["created_at:desc"],
            }

            if filters:
                search_body["filter"] = " AND ".join(filters)

            result = self._request(
                "POST", f"/indexes/{self._message_index}/search", search_body
            )

            results = []
            for hit in result.get("hits", []):
                results.append(
                    MessageSearchResult(
                        id=int(hit.get("message_id", 0)),
                        message_id=int(hit.get("message_id", 0)),
                        content=hit.get("content", ""),
                        author_id=int(hit.get("author_id", 0)),
                        conversation_id=int(hit.get("conversation_id", 0)),
                        server_id=int(hit["server_id"])
                        if hit.get("server_id")
                        else None,
                        channel_id=int(hit["channel_id"])
                        if hit.get("channel_id")
                        else None,
                        created_at=hit.get("created_at", 0),
                        has_attachments=hit.get("has_attachments", False),
                        is_pinned=hit.get("is_pinned", False),
                        score=1.0,
                    )
                )

            return results

        except Exception as e:
            logger.error(f"Meilisearch search failed: {e}")
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

            self._request("POST", f"/indexes/{self._user_index}/documents", [doc])
            return True

        except Exception as e:
            logger.error(f"Failed to index user {user.user_id}: {e}")
            return False

    def remove_user(self, user_id: int) -> bool:
        """Remove a user from the index."""
        try:
            self._request("DELETE", f"/indexes/{self._user_index}/documents/{user_id}")
            return True
        except Exception:
            return False

    def search_users(
        self,
        query: str,
        limit: int = 25,
        offset: int = 0,
    ) -> List[UserSearchResult]:
        """Search users using Meilisearch."""
        try:
            result = self._request(
                "POST",
                f"/indexes/{self._user_index}/search",
                {"q": query, "limit": limit, "offset": offset},
            )

            results = []
            for hit in result.get("hits", []):
                results.append(
                    UserSearchResult(
                        id=int(hit.get("user_id", 0)),
                        user_id=int(hit.get("user_id", 0)),
                        username=hit.get("username", ""),
                        display_name=hit.get("display_name") or None,
                        is_bot=hit.get("is_bot", False),
                        score=1.0,
                    )
                )

            return results

        except Exception as e:
            logger.error(f"Meilisearch user search failed: {e}")
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

            self._request("POST", f"/indexes/{self._server_index}/documents", [doc])
            return True

        except Exception as e:
            logger.error(f"Failed to index server {server.server_id}: {e}")
            return False

    def remove_server(self, server_id: int) -> bool:
        """Remove a server from the index."""
        try:
            self._request(
                "DELETE", f"/indexes/{self._server_index}/documents/{server_id}"
            )
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
        """Search servers using Meilisearch."""
        try:
            filters = []

            if public_only:
                filters.append("is_public = true")
            if category:
                filters.append(f'category = "{category}"')

            search_body = {
                "q": query,
                "limit": limit,
                "offset": offset,
            }

            if filters:
                search_body["filter"] = " AND ".join(filters)

            result = self._request(
                "POST", f"/indexes/{self._server_index}/search", search_body
            )

            results = []
            for hit in result.get("hits", []):
                results.append(
                    ServerSearchResult(
                        id=int(hit.get("server_id", 0)),
                        server_id=int(hit.get("server_id", 0)),
                        name=hit.get("name", ""),
                        description=hit.get("description") or None,
                        category=hit.get("category") or None,
                        categories=[hit.get("category")] if hit.get("category") else [],
                        tags=hit.get("tags", []),
                        member_count=hit.get("member_count", 0),
                        score=1.0,
                    )
                )

            return results

        except Exception as e:
            logger.error(f"Meilisearch server search failed: {e}")
            return []

    def get_stats(self) -> Dict[str, Any]:
        """Get indexer statistics."""
        try:
            msg_stats = self._request("GET", f"/indexes/{self._message_index}/stats")
            user_stats = self._request("GET", f"/indexes/{self._user_index}/stats")
            server_stats = self._request("GET", f"/indexes/{self._server_index}/stats")

            return {
                "backend": "meilisearch",
                "message_count": msg_stats.get("numberOfDocuments", 0),
                "user_count": user_stats.get("numberOfDocuments", 0),
                "server_count": server_stats.get("numberOfDocuments", 0),
                "healthy": True,
            }

        except Exception as e:
            return {
                "backend": "meilisearch",
                "healthy": False,
                "error": str(e),
            }
