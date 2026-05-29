"""
Feature expansion routes - packaged from the monolithic feature_routes.py.
"""

from fastapi import APIRouter

# Re-export the existing user/admin features router (migrated from features.py)
from .tier_features import router as features_router

from .bookmarks import router as bookmarks_router
from .scheduled_messages import router as scheduled_messages_router
from .forwarding import router as forwarding_router
from .voice import router as voice_router
from .profiles import router as profiles_router
from .push import router as push_router
from .last_chat import router as last_chat_router
from .slowmode import router as slowmode_router
from .audit_logs import router as audit_logs_router
from .reports import router as reports_router
from .onboarding import router as onboarding_router

feature_expansion_router = APIRouter(tags=["Feature Expansion"])
feature_expansion_router.include_router(bookmarks_router)
feature_expansion_router.include_router(scheduled_messages_router)
feature_expansion_router.include_router(forwarding_router)
feature_expansion_router.include_router(voice_router)
feature_expansion_router.include_router(profiles_router)
feature_expansion_router.include_router(push_router)
feature_expansion_router.include_router(last_chat_router)
feature_expansion_router.include_router(slowmode_router)
feature_expansion_router.include_router(audit_logs_router)
feature_expansion_router.include_router(reports_router)
feature_expansion_router.include_router(onboarding_router)

__all__ = [
    "features_router",
    "feature_expansion_router",
]
