"""
Message routes - Message CRUD endpoints.

This module aggregates message-related routes from sub-modules:
- messages_crud.py: Core message CRUD operations (create, read, update, delete)
- messages_list.py: List operations (get channel messages, get pinned messages)
- pins.py: Pin/unpin operations
- search.py: Message search functionality
- read_receipts.py: Read receipts and unread counts
- typing.py: Typing indicators

The public API remains unchanged - all routes are exposed through this router.
"""

from fastapi import APIRouter

# Import routers from sub-modules
from .messages_crud import router as crud_router
from .messages_list import router as list_router
from .pins import router as pins_router
from .search import router as search_router
from .read_receipts import router as read_receipts_router
from .typing import router as typing_router
from .messages import get_msg_id

# Create main router and include all sub-routers
router = APIRouter(tags=["Messages"])

# Include all routers - this maintains the exact same public API
router.include_router(search_router)
router.include_router(read_receipts_router)
router.include_router(crud_router)
router.include_router(list_router)
router.include_router(pins_router)
router.include_router(typing_router)

__all__ = ["router", "get_msg_id"]
