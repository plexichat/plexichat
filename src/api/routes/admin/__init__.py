"""
Admin API router - combines modular routes.
"""

from fastapi import APIRouter
from src.api.schemas.admin import DeviceTrustOverviewResponse
from src.api.schemas.common import ErrorResponse

from .auth import router as auth_router
from .dashboard import router as dashboard_router
from .tickets import router as tickets_router
from .users import router as users_router
from .security import router as security_router
from .device_trust import router as device_trust_router  # noqa: F401 (re-exported)
from .device_trust import get_device_trust_admin_overview
from .moderation import router as moderation_router
from .telemetry import router as telemetry_router
from .logs import router as logs_router
from .database import router as database_router
from .ui import router as ui_router
from .reindex import router as reindex_router
from .migrations import router as migrations_router
from .roles import router as roles_router
from .approvals import router as approvals_router
from .bots import router as admin_bots_router
from .audit import router as audit_router
from .licensing import router as licensing_router
from .plexijoin import router as plexijoin_router
from .capabilities import router as capabilities_router
from .artifacts import router as admin_artifacts_router
from .data_export import router as data_export_router

router = APIRouter(
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
    },
)

# Register routes
router.include_router(auth_router)
router.include_router(dashboard_router)
router.include_router(tickets_router)
router.include_router(users_router)
router.include_router(security_router)

# Device-trust overview is registered directly on the admin router with the
# full path ``/device-trust/overview`` (mounted under ``/admin`` this yields
# ``/admin/device-trust/overview``). Registering the route directly (instead
# of a prefixed include_router) keeps the path visible to route introspection
# tests and keeps the handler owning its own scope guard.
router.get(
    "/device-trust/overview",
    response_model=DeviceTrustOverviewResponse,
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {
            "model": ErrorResponse,
            "description": "Missing admin.security.devices scope",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)(get_device_trust_admin_overview)

router.include_router(moderation_router)
router.include_router(telemetry_router)
router.include_router(logs_router)
router.include_router(database_router)
router.include_router(roles_router)
router.include_router(approvals_router)
router.include_router(ui_router)
router.include_router(reindex_router)
router.include_router(migrations_router)
router.include_router(admin_bots_router)
router.include_router(audit_router)
router.include_router(licensing_router)
router.include_router(plexijoin_router)
router.include_router(capabilities_router)
router.include_router(admin_artifacts_router)
router.include_router(data_export_router)
