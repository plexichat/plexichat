from typing import List, Optional

from .base import SearchManagerBase
from ..models import (
    ServerCategory,
    ServerListing,
    ServerSearchResult,
    VerificationLevel,
)


class DiscoveryMixin(SearchManagerBase):
    def list_public_servers(
        self,
        category: Optional[str] = None,
        sort_by: str = "member_count",
        limit: int = 25,
        offset: int = 0,
    ) -> List[ServerListing]:
        return self._discovery.list_public_servers(category, sort_by, limit, offset)

    def get_server_categories(self) -> List[ServerCategory]:
        return self._discovery.get_server_categories()

    def list_server(
        self,
        user_id: int,
        server_id: int,
        category: str,
        description: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> ServerListing:
        return self._discovery.list_server(
            user_id, server_id, category, description, tags
        )

    def unlist_server(self, user_id: int, server_id: int) -> bool:
        return self._discovery.unlist_server(user_id, server_id)

    def verify_server(self, server_id: int, level: VerificationLevel) -> bool:
        return self._discovery.verify_server(server_id, level)

    def bump_server(self, user_id: int, server_id: int) -> bool:
        return self._discovery.bump_server(user_id, server_id)

    def _enrich_server_results(
        self,
        results: List[ServerSearchResult],
    ) -> List[ServerSearchResult]:
        server_ids = [r.server_id for r in results]
        if not server_ids:
            return results

        listings_map = self._discovery.get_listings_bulk(server_ids)

        for result in results:
            listing = listings_map.get(result.server_id)
            if listing:
                result.verification_level = listing.verification_level
                result.is_verified = listing.is_verified

        return results
