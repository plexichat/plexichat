"""
API routes - Route registration for all endpoints.
"""

from fastapi import APIRouter

from .health import router as health_router
from .auth import router as auth_router
from .users import router as users_router
from .servers import router as servers_router
from .channels import router as channels_router
from .messages import router as messages_router
from .relationships import router as relationships_router
from .presence import router as presence_router
from .reactions import router as reactions_router
from .emojis import router as emojis_router
from .webhooks import router as webhooks_router
from .version import router as version_router
from .settings import router as settings_router
from .feedback import router as feedback_router
from .notifications import router as notifications_router
from .docs import router as docs_router, is_docs_enabled, clear_docs_cache, get_docs_stats


def create_api_router() -> APIRouter:
    """Create and configure the main API router."""
    api_router = APIRouter()
    
    api_router.include_router(health_router, tags=["Health"])
    api_router.include_router(version_router, tags=["Version"])
    api_router.include_router(auth_router, prefix="/auth", tags=["Authentication"])
    api_router.include_router(users_router, prefix="/users", tags=["Users"])
    api_router.include_router(servers_router, prefix="/servers", tags=["Servers"])
    api_router.include_router(emojis_router, prefix="/servers", tags=["Emojis"])
    api_router.include_router(channels_router, prefix="/channels", tags=["Channels"])
    api_router.include_router(messages_router, tags=["Messages"])
    api_router.include_router(relationships_router, prefix="/relationships", tags=["Relationships"])
    api_router.include_router(presence_router, tags=["Presence"])
    api_router.include_router(reactions_router, tags=["Reactions"])
    api_router.include_router(webhooks_router, prefix="/webhooks", tags=["Webhooks"])
    api_router.include_router(settings_router, prefix="/users/@me/settings", tags=["Settings"])
    api_router.include_router(feedback_router, tags=["Feedback"])
    api_router.include_router(notifications_router, tags=["Notifications"])
    
    return api_router


def create_docs_router() -> APIRouter:
    """Create the documentation router (mounted separately)."""
    return docs_router


__all__ = [
    "create_api_router",
    "create_docs_router",
    "is_docs_enabled",
    "clear_docs_cache",
    "get_docs_stats",
]
