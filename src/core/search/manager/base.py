from typing import Any, Dict, List, Optional

import utils.config as config
import utils.logger as logger
from src.core.base import BaseManager

from .. import models as search_models
from ..exceptions import (
    AlreadyListedError,
    CategoryNotFoundError,
    CooldownError,
    MinimumMembersError,
    NotListedError,
    SearchError,
    SearchLimitError,
    SearchRateLimitError,
)
from ..indexer.base import IndexerConfig
from ..indexer import (
    ElasticsearchIndexer,
    MeilisearchIndexer,
    PostgresIndexer,
    SQLiteFTS5Indexer,
)
from ..models import (
    MessageSearchResult,
    ServerSearchResult,
    UserSearchResult,
)
from ..query import FilterProcessor, QueryParser, RankingEngine
from ..discovery import DiscoveryManager


class SearchManagerBase(BaseManager):
    models = search_models

    SearchError = SearchError
    SearchLimitError = SearchLimitError
    MinimumMembersError = MinimumMembersError
    CooldownError = CooldownError
    AlreadyListedError = AlreadyListedError
    NotListedError = NotListedError
    CategoryNotFoundError = CategoryNotFoundError
    SearchRateLimitError = SearchRateLimitError

    _messaging: Any
    _servers: Any
    _config: Dict[str, Any]
    _indexer: Any
    _query_parser: QueryParser
    _filter_processor: FilterProcessor
    _ranking_engine: RankingEngine
    _discovery: DiscoveryManager
    _search_rate_window_started_ms: Dict[int, float]
    _search_rate_count: Dict[int, int]

    def _get_manager(self):
        return self

    def __init__(
        self, db, auth_module=None, messaging_module=None, servers_module=None
    ):
        super().__init__(db, auth_module)
        self._messaging = messaging_module
        self._servers = servers_module
        self._config = self._load_config()

        self._indexer = self._create_indexer()
        self._indexer.initialize()

        self._query_parser = QueryParser()
        self._filter_processor = FilterProcessor(db, auth_module, servers_module)
        self._ranking_engine = RankingEngine()

        self._discovery = DiscoveryManager(db, servers_module)

        self._search_rate_window_started_ms: Dict[int, float] = {}
        self._search_rate_count: Dict[int, int] = {}

        logger.info("Search module initialized")

    def _load_config(self) -> Dict[str, Any]:
        defaults = {
            "backend": "sqlite_fts5",
            "batch_size": 100,
            "write_time_indexing": True,
            "result_limit": 100,
            "elasticsearch": {
                "hosts": ["http://localhost:9200"],
                "index_prefix": "plexichat",
            },
            "meilisearch": {
                "host": "http://localhost:7700",
                "api_key": None,
                "index_prefix": "plexichat",
            },
            "discovery": {
                "min_members_for_listing": 10,
                "bump_cooldown_hours": 4,
                "max_tags": 10,
            },
            "rate_limit_per_minute": 60,
        }

        search_config = config.get("search", {})
        return {**defaults, **search_config}

    def _create_indexer(self):
        backend = self._config.get("backend", "sqlite_fts5")

        indexer_config = IndexerConfig(
            batch_size=self._config.get("batch_size", 100),
            write_time_indexing=self._config.get("write_time_indexing", True),
            result_limit=self._config.get("result_limit", 100),
        )

        db_type = getattr(self._db, "type", "sqlite")

        if db_type == "postgres" and backend == "sqlite_fts5":
            logger.info("Postgres database detected, using Postgres search indexer")
            return PostgresIndexer(self._db, indexer_config)

        if backend == "elasticsearch":
            es_config = self._config.get("elasticsearch", {})
            return ElasticsearchIndexer(
                hosts=es_config.get("hosts", ["http://localhost:9200"]),
                index_prefix=es_config.get("index_prefix", "plexichat"),
                config=indexer_config,
            )

        if backend == "meilisearch":
            ms_config = self._config.get("meilisearch", {})
            return MeilisearchIndexer(
                host=ms_config.get("host", "http://localhost:7700"),
                api_key=ms_config.get("api_key"),
                index_prefix=ms_config.get("index_prefix", "plexichat"),
                config=indexer_config,
            )

        return SQLiteFTS5Indexer(self._db, indexer_config)

    def clear_cache(self):
        self._search_rate_window_started_ms.clear()
        self._search_rate_count.clear()

    def _check_rate_limit(self, user_id: int) -> None: ...

    def _get_accessible_conversations(
        self,
        user_id: int,
        conversation_id: Optional[int] = None,
        server_id: Optional[int] = None,
        channel_id: Optional[int] = None,
    ) -> List[int]: ...

    def _can_access_server(self, user_id: int, server_id: int) -> bool: ...

    def _can_access_conversation(self, user_id: int, conversation_id: int) -> bool: ...

    def _can_access_channel(self, user_id: int, channel_id: int) -> bool: ...

    def _get_server_member_ids(self, server_id: int) -> set: ...

    def _get_user_server_ids(self, user_id: int) -> set: ...

    def _get_user_servers_map(self, user_ids: List[int]) -> Dict[int, set]: ...

    def _enrich_message_results(
        self,
        results: List[MessageSearchResult],
        user_id: int,
    ) -> List[MessageSearchResult]: ...

    def _enrich_user_results(
        self,
        results: List[UserSearchResult],
        user_id: int,
    ) -> List[UserSearchResult]: ...

    def _enrich_server_results(
        self,
        results: List[ServerSearchResult],
    ) -> List[ServerSearchResult]: ...

    def _get_names(
        self,
        ids: set,
        cache_prefix: str,
        table: str,
        id_col: str,
        name_col: str,
        ttl: int = 300,
    ) -> Dict[int, str]: ...
