"""
User Settings routes - Cloud-synced key-value store for user preferences.
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Dict

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo

import utils.logger as logger

router = APIRouter()


class SettingValue(BaseModel):
    """Request body for setting a value."""
    value: str = Field(..., max_length=10000, description="Setting value")


class SettingResponse(BaseModel):
    """Response for a single setting."""
    key: str
    value: str
    created_at: int
    updated_at: int


class SettingsResponse(BaseModel):
    """Response for all settings."""
    settings: Dict[str, str]
    count: int
    limit: int


@router.get("", response_model=SettingsResponse)
async def get_all_settings(current_user: TokenInfo = Depends(get_current_user)):
    """
    Get all settings for the current user.
    
    Returns all key-value pairs stored for the user.
    """
    settings_module = api.get_settings()
    if not settings_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}}
        )

    try:
        settings = settings_module.get_all_settings(current_user.user_id)
        count = len(settings)

        logger.debug(f"Retrieved {count} settings for user {current_user.user_id}")

        return SettingsResponse(
            settings=settings,
            count=count,
            limit=100  # Default limit from SettingsConfig
        )
    except Exception as e:
        logger.error(f"Failed to get settings for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.get("/{key}", response_model=SettingResponse)
async def get_setting(key: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Get a specific setting by key.
    
    Returns the setting value and metadata.
    """
    settings_module = api.get_settings()
    if not settings_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}}
        )

    try:
        # Get the full setting object
        settings_list = settings_module.get_settings_list(current_user.user_id)
        setting = next((s for s in settings_list if s.key == key), None)

        if not setting:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": f"Setting '{key}' not found"}}
            )

        return SettingResponse(
            key=setting.key,
            value=setting.value,
            created_at=setting.created_at,
            updated_at=setting.updated_at
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get setting '{key}' for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put("/{key}", response_model=SettingResponse)
async def set_setting(
    key: str,
    body: SettingValue,
    current_user: TokenInfo = Depends(get_current_user)
):
    """
    Set a setting value.
    
    Creates or updates the setting with the given key.
    """
    settings_module = api.get_settings()
    if not settings_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}}
        )

    try:
        setting = settings_module.set_setting(current_user.user_id, key, body.value)

        logger.info(f"Set setting '{key}' for user {current_user.user_id}")

        return SettingResponse(
            key=setting.key,
            value=setting.value,
            created_at=setting.created_at,
            updated_at=setting.updated_at
        )
    except Exception as e:
        exc_name = type(e).__name__

        if "LimitExceeded" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(e)}}
            )
        elif "KeyTooLong" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(e)}}
            )
        elif "ValueTooLong" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(e)}}
            )
        elif "KeyReserved" in exc_name:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": str(e)}}
            )

        logger.error(f"Failed to set setting '{key}' for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete("/{key}")
async def delete_setting(key: str, current_user: TokenInfo = Depends(get_current_user)):
    """
    Delete a setting.
    
    Removes the setting with the given key.
    """
    settings_module = api.get_settings()
    if not settings_module:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}}
        )

    try:
        deleted = settings_module.delete_setting(current_user.user_id, key)

        if not deleted:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": f"Setting '{key}' not found"}}
            )

        logger.info(f"Deleted setting '{key}' for user {current_user.user_id}")

        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete setting '{key}' for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": str(e)}}
        )
