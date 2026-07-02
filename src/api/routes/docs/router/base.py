"""
Base class for the documentation router.

Provides shared cache helpers and state management used by all mixins.
"""

import time
from pathlib import Path
from typing import ClassVar, Dict, Any, Optional

from ..config import DocsConfig, get_docs_config, is_docs_enabled


class DocsRouterBase:
    DOCS_ROOT: ClassVar[Path] = Path(__file__).resolve().parents[5] / "docs"

    _docs_cache: ClassVar[Dict[str, tuple[str, float]]] = {}
    _html_cache: ClassVar[Dict[str, tuple[str, float]]] = {}

    def _get_cached_value(
        self, cache: Dict[str, tuple[str, float]], key: str, ttl_seconds: int
    ) -> Optional[str]:
        entry = cache.get(key)
        if entry is None:
            return None
        value, cached_at = entry
        if (time.time() - cached_at) > ttl_seconds:
            cache.pop(key, None)
            return None
        return value

    def _set_cached_value(
        self,
        cache: Dict[str, tuple[str, float]],
        key: str,
        value: str,
        max_entries: int,
    ) -> None:
        cache[key] = (value, time.time())
        while len(cache) > max_entries:
            cache.pop(next(iter(cache)))

    def _build_html_cache_key(
        self, source_key: str, title: str, current_path: str, conf: DocsConfig
    ) -> str:
        return "|".join((source_key, title, current_path, repr(conf)))

    def _doc_path(self, relative_path: str) -> Path:
        return self.DOCS_ROOT / relative_path

    def clear_docs_cache(self) -> bool:
        self._docs_cache.clear()
        self._html_cache.clear()
        return True

    def get_docs_stats(self) -> Dict[str, Any]:
        return {
            "cache": {
                "docs_entries": len(self._docs_cache),
                "html_entries": len(self._html_cache),
            },
            "config": {
                "enabled": is_docs_enabled(),
                "path": get_docs_config().path,
            },
        }
