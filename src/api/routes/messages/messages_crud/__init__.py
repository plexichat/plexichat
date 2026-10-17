"""
Message CRUD routes sub-package.

Provides a modular message CRUD router with mixin-based organisation.
All route handlers are collected into the MessagesCRUDRouter class and
registered on a module-level APIRouter.
"""

from fastapi import APIRouter

from .composer import MessagesCRUDRouter

router = APIRouter(tags=["Messages"])

MessagesCRUDRouter().register_routes(router)

__all__ = [
    "router",
    "MessagesCRUDRouter",
]
