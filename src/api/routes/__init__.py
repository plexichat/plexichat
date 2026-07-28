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
from .threads import router as threads_router
from .emojis import router as emojis_router
from .search import router as search_router
from .webhooks import router as webhooks_router
from .applications import router as applications_router
from .bots import router as bots_router
from .version import router as version_router
from .settings import router as settings_router
from .feedback import router as feedback_router
from .notifications import router as notifications_router
from .polls import router as polls_router
from .stickers import router as stickers_router
from .docs import (
    router as docs_router,
    is_docs_enabled,
    clear_docs_cache,
    get_docs_stats,
)
from .telemetry import router as telemetry_router
from .admin import router as admin_router
from .features import features_router, feature_expansion_router
from .voice import router as voice_router
from .avatars import router as avatars_router
from .media import router as media_router
from .reports import router as reports_router
from .qr import router as qr_router
from .help import router as help_router, robots_router
from .config import router as config_router
from .capabilities import router as capabilities_router
from .artifacts import router as artifacts_router

import utils.config as config


def create_api_router() -> APIRouter:
    """Create and configure the main API router."""
    api_router = APIRouter()

    api_router.include_router(health_router)
    api_router.include_router(version_router)
    api_router.include_router(config_router)
    api_router.include_router(auth_router, prefix="/auth")
    api_router.include_router(users_router, prefix="/users")
    api_router.include_router(servers_router, prefix="/servers")
    api_router.include_router(emojis_router, prefix="/servers")
    api_router.include_router(messages_router)
    api_router.include_router(search_router)
    api_router.include_router(channels_router, prefix="/channels")
    api_router.include_router(relationships_router, prefix="/relationships")
    api_router.include_router(presence_router)
    api_router.include_router(reactions_router)
    api_router.include_router(threads_router)
    api_router.include_router(webhooks_router)
    api_router.include_router(bots_router)
    api_router.include_router(applications_router)
    api_router.include_router(settings_router, prefix="/users/@me/settings")
    api_router.include_router(feedback_router)
    api_router.include_router(notifications_router)
    api_router.include_router(telemetry_router)
    api_router.include_router(voice_router)
    api_router.include_router(polls_router)

    # Include admin router with configurable path
    admin_config = config.get("admin_ui", {})
    admin_path = admin_config.get("path", "/admin")
    api_router.include_router(admin_router, prefix=admin_path)

    # Include features router (admin endpoints + user features)
    api_router.include_router(features_router)

    # Include avatars router
    api_router.include_router(avatars_router, prefix="/avatars")

    # Include media router
    api_router.include_router(media_router)

    # Include reports router
    api_router.include_router(reports_router)

    # Include QR router
    api_router.include_router(qr_router)

    # Include Stickers router
    api_router.include_router(stickers_router)

    # Include help router
    api_router.include_router(robots_router)
    api_router.include_router(help_router, prefix="/help")

    # Include feature expansion routes under /features prefix
    api_router.include_router(feature_expansion_router, prefix="/features")

    # Include capabilities router (artifact feature availability state)
    api_router.include_router(capabilities_router)

    # Include artifacts router (artifact CRUD + inline transcript emission)
    api_router.include_router(artifacts_router)

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
    "feature_expansion_router",
]
