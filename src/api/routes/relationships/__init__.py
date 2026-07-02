"""
Relationship routes - Friend and block management endpoints.

Refactored from a monolithic module into a class-based mixin architecture.
See README.md for architecture details.
"""

from fastapi import APIRouter

from .composer import RelationshipsRouter

router = APIRouter(tags=["Relationships"])

_routes = RelationshipsRouter()
_routes.register_routes(router)

__all__ = [
    "router",
]
