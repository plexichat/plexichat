from .base import SearchManagerBase
from .message_search import MessageSearchMixin
from .message_indexing import MessageIndexingMixin
from .user_search import UserSearchMixin
from .server_search import ServerSearchMixin
from .discovery import DiscoveryMixin
from .query_utils import QueryUtilsMixin
from .rate_limiting import RateLimitMixin
from .access import AccessControlMixin
from .enrichment import EnrichmentMixin


class SearchManager(
    MessageSearchMixin,
    MessageIndexingMixin,
    UserSearchMixin,
    ServerSearchMixin,
    DiscoveryMixin,
    QueryUtilsMixin,
    RateLimitMixin,
    AccessControlMixin,
    EnrichmentMixin,
    SearchManagerBase,
):
    pass
