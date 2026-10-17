"""
User routes - User profile endpoints.

Refactored from a monolithic module into a class-based mixin architecture.
See README.md for architecture details.
"""

from fastapi import APIRouter

from .composer import UsersRouter
from .helpers import _get_user_cached, _user_to_public_response

router = APIRouter(tags=["Users"])

_routes = UsersRouter()
_routes.register_routes(router)

__all__ = [
    "router",
    "_get_user_cached",
    "_user_to_public_response",
]
