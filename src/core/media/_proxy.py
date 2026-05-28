# pyright: reportAttributeAccessIssue=false
"""
External URL proxy methods mixed into MediaManager.
"""

import logging
from typing import Optional, Tuple

from .models import ProxiedContent
from .exceptions import MediaError
from .security import ExternalProxy

logger = logging.getLogger(__name__)


class _ProxyMixin:
    """Proxy methods mixed into MediaManager."""

    def _init_proxy(self) -> Optional[ExternalProxy]:
        if not self._config.get("proxy_enabled", True):
            return None
        try:
            return ExternalProxy(
                storage_backend=self._storage,
                db=self._db,
                cache_ttl=self._config.get("proxy_cache_ttl", 86400),
                max_size=self._config.get("proxy_max_size", 10 * 1024 * 1024),
                buffer_size=self._config.get("proxy_buffer_size", 65536),
            )
        except Exception as e:
            logger.warning(f"External proxy unavailable: {e}")
            return None

    def proxy_url(self, url: str, force_refresh: bool = False) -> ProxiedContent:
        if not self._proxy:
            raise MediaError("Proxy not available")
        return self._proxy.fetch(url, force_refresh)

    def get_proxied_content(self, url: str) -> Tuple[bytes, str]:
        if not self._proxy:
            raise MediaError("Proxy not available")
        return self._proxy.get_content(url)
