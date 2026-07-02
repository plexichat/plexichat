"""
Admin user management routes package.

Refactored from a monolithic module into a class-based mixin architecture.
See README.md for architecture details.
"""

from fastapi import APIRouter

from .composer import AdminUsersRouter

router = APIRouter()

_routes = AdminUsersRouter()
_routes.register_routes(router)

__all__ = [
    "router",
]
