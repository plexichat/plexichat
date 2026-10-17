"""
Documentation routes - Serve API documentation with dynamic rate limit info.

This module provides a configurable documentation server that:
- Serves markdown documentation as HTML with a modern sidebar layout
- Dynamically loads rate limits from actual config
- Has its own configurable rate limiting
- Supports caching, theming, and logging

This is a compatibility shim that imports from the new modular structure.
All actual implementation has been moved to src/api/routes/docs/ subdirectory.
"""

# Import from the new modular structure
from .docs.router import router, clear_docs_cache, get_docs_stats
from .docs.config import get_docs_config, is_docs_enabled, DocsConfig
from .docs.openapi import render_swagger_ui_page, render_redoc_page

# Re-export for backward compatibility
__all__ = [
    "router",
    "clear_docs_cache",
    "get_docs_stats",
    "get_docs_config",
    "is_docs_enabled",
    "DocsConfig",
    "render_swagger_ui_page",
    "render_redoc_page",
]
