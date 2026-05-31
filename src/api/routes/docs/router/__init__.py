"""
Documentation routes package.

Provides a modular documentation router with mixin-based organisation.
All route handlers are collected into the DocsRouter class and registered
on a module-level APIRouter.
"""

from fastapi import APIRouter

from .composer import DocsRouter

router = APIRouter(tags=["Documentation"])

_docs_router = DocsRouter()
_docs_router.register_routes(router)

clear_docs_cache = _docs_router.clear_docs_cache
get_docs_stats = _docs_router.get_docs_stats

__all__ = [
    "router",
    "clear_docs_cache",
    "get_docs_stats",
    "DocsRouter",
]
