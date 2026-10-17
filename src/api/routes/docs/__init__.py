"""
Documentation routes module.

This module provides a modular documentation system with separated concerns.
"""

from .router import router, clear_docs_cache, get_docs_stats
from .config import get_docs_config, is_docs_enabled, DocsConfig

__all__ = [
    "router",
    "clear_docs_cache",
    "get_docs_stats",
    "get_docs_config",
    "is_docs_enabled",
    "DocsConfig",
]
