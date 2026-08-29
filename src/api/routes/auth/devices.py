from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, ConfigDict, Field

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import SnowflakeID, ErrorResponse, SuccessResponse

router = APIRouter()


class DeviceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: SnowflakeID
    name: Optional[str] = None
    device_type: Optional[str] = None
    trusted: bool = False
    first_seen_at: int = 0
    last_seen_at: int = 0


class RenameDeviceRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    name: str = Field(..., min_length=1, max_length=64)


class TrustDeviceRequest(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    # The fingerprint of the device to (un)trust. Must be a device this user
    # has actually authenticated from; trusting an unknown fingerprint is a
    # no-op (404), never a silent success.
    fingerprint: str = Field(..., min_length=8, max_length=256)


def _get_devices(user_id: int):
    auth = api.get_auth()
    if not auth:
        logger.error("Auth module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Auth module not available"}},
        )
    return auth, auth.get_devices(user_id)


@router.get(
    "/devices",
    response_model=List[DeviceResponse],
    summary="List known devices",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_devices(
    current_user: TokenInfo = Depends(get_current_user),
) -> List[DeviceResponse]:
    _, devices = _get_devices(current_user.user_id)
    return [
        DeviceResponse(
            id=SnowflakeID(d.id),
            name=d.name,
            device_type=d.device_type,
            trusted=bool(getattr(d, "trusted", False)),
            first_seen_at=d.first_seen_at,
            last_seen_at=d.last_seen_at,
        )
        for d in devices
    ]


@router.patch(
    "/devices/{device_id:int}",
    response_model=SuccessResponse,
    summary="Rename a device",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid device ID or name"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Device not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def rename_device(
    device_id: int,
    body: RenameDeviceRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    auth, _ = _get_devices(current_user.user_id)
    if not auth.rename_device(current_user.user_id, device_id, body.name):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Device not found"}},
        )
    return SuccessResponse(success=True, message=None)


@router.delete(
    "/devices/{device_id:int}",
    response_model=SuccessResponse,
    summary="Revoke (forget) a device",
    description=(
        "Removes the device and revokes all sessions created from it. "
        "A revoked device loses trusted status implicitly."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Invalid device ID"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Device not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_device(
    device_id: int,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    auth, _ = _get_devices(current_user.user_id)
    if not auth.revoke_device(current_user.user_id, device_id):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Device not found"}},
        )
    return SuccessResponse(success=True, message=None)


@router.post(
    "/devices/trust",
    response_model=SuccessResponse,
    summary="Mark a device as trusted (2FA skip)",
    description=(
        "Trusted devices skip the 2FA challenge at login. Only devices this "
        "account has previously authenticated from can be trusted; trusting "
        "an unknown fingerprint returns 404."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Invalid fingerprint"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Device not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def trust_device(
    body: TrustDeviceRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    auth, _ = _get_devices(current_user.user_id)
    if not auth.trust_device(current_user.user_id, body.fingerprint):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Device not found"}},
        )
    return SuccessResponse(success=True, message=None)


@router.post(
    "/devices/untrust",
    response_model=SuccessResponse,
    summary="Remove trusted status from a device",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid fingerprint"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Device not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def untrust_device(
    body: TrustDeviceRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    auth, _ = _get_devices(current_user.user_id)
    if not auth.untrust_device(current_user.user_id, body.fingerprint):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Device not found"}},
        )
    return SuccessResponse(success=True, message=None)
