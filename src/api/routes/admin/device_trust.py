"""
Admin device-trust & session-duration routes.

GET /admin/device-trust/overview — read-only snapshot of device trust state
and effective session durations for every user with devices or live sessions.
"""

from fastapi import APIRouter, Request
from typing import Any, Dict

from src.api.schemas.admin import DeviceTrustOverviewResponse
from src.api.schemas.common import ErrorResponse
from .utils import check_host_restriction, require_admin_permission
from .device_overview import get_device_trust_overview
import utils.logger as logger

router = APIRouter()


@router.get(
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
)
async def get_device_trust_admin_overview(request: Request):
    """Admin snapshot: device trust + effective session durations per user."""
    check_host_restriction(request)
    require_admin_permission(request, "admin.security.devices")

    from src.core.auth._lazy import _get_auth_manager

    auth_manager = _get_auth_manager().get_instance()
    config: Dict[str, Any] = auth_manager._config

    try:
        overview = get_device_trust_overview(auth_manager, config)
    except Exception as e:
        logger.error(f"Device-trust overview failed: {e}", exc_info=True)
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="Internal server error")

    return DeviceTrustOverviewResponse(**overview)
