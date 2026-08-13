"""
User Settings routes - Cloud-synced key-value store for user preferences.
"""

import time
import json
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Depends, Body

import src.api as api
from src.core.database import cached, invalidate_pattern
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.settings import (
    SettingValue,
    SettingResponse,
    SettingsResponse,
    BulkSettingsResponse,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.settings.exceptions import (
    SettingsLimitExceeded,
    SettingsKeyTooLong,
    SettingsValueTooLong,
    SettingsKeyReserved,
)

import utils.logger as logger

router = APIRouter(tags=["User Settings"])


def _invalidate_user_settings_cache(user_id: int) -> None:
    """Invalidate all cached settings reads for a user (both list and per-key)."""
    # GET /            -> user_settings_all_api:current_user:TokenInfo:<uid>
    # GET /{key}       -> user_setting_api:current_user:TokenInfo:<uid>:key:<value>
    invalidate_pattern(f"user_settings_all_api:*:TokenInfo:{user_id}")
    invalidate_pattern(f"user_setting_api:*:TokenInfo:{user_id}:*")


@router.get(
    "",
    response_model=SettingsResponse,
    summary="Get all settings",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60, prefix="user_settings_all_api")
def get_all_settings(
    current_user: TokenInfo = Depends(get_current_user),
) -> SettingsResponse:
    """
    Get all settings for the current user.

    Returns all key-value pairs stored for the user.
    """
    settings_module = api.get_settings()
    if not settings_module:
        logger.error("Settings module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}},
        )

    try:
        settings = settings_module.get_all_settings(current_user.user_id)
        count = len(settings)

        logger.debug(f"Retrieved {count} settings for user {current_user.user_id}")

        return SettingsResponse(
            settings=settings,
            count=count,
            limit=settings_module.get_setting_limit(),
        )
    except Exception as e:
        logger.error(
            f"Failed to get settings for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put(
    "/bulk",
    response_model=BulkSettingsResponse,
    summary="Bulk update settings",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def bulk_update_settings(
    body: Dict[str, Any] = Body(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> BulkSettingsResponse:
    """
    Update multiple settings at once.

    Accepts a dictionary of key-value pairs.
    """
    settings_module = api.get_settings()
    presence_module = api.get_presence()

    if not settings_module:
        logger.error("Settings module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}},
        )

    failures: Dict[str, str] = {}
    updated = 0
    try:
        for key, raw_value in body.items():
            # Convert value to string for storage
            value = str(raw_value) if not isinstance(raw_value, str) else raw_value

            # Special case for focused channel
            if key == "_focused_channel" and presence_module:
                try:
                    data = json.loads(value)
                    cid = data.get("channel_id")
                    sid = data.get("server_id")

                    presence_module.set_focused_channel(
                        current_user.user_id,
                        channel_id=int(cid) if cid and str(cid) != "0" else None,
                        server_id=int(sid) if sid and str(sid) != "0" else None,
                    )
                    updated += 1
                except Exception as e:
                    failures[key] = f"Failed to update focused channel: {e}"
                continue

            # Standard settings
            try:
                settings_module.set_setting(current_user.user_id, key, value)
                updated += 1
            except (
                SettingsLimitExceeded,
                SettingsKeyTooLong,
                SettingsValueTooLong,
                SettingsKeyReserved,
            ) as e:
                failures[key] = str(e)
            except Exception as e:
                failures[key] = f"Unexpected error: {e}"

        _invalidate_user_settings_cache(current_user.user_id)

        return BulkSettingsResponse(
            success=len(failures) == 0,
            updated=updated,
            failed=len(failures),
            failures=failures,
        )
    except Exception as e:
        logger.error(f"Bulk update failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{key}",
    response_model=SettingResponse,
    summary="Get a specific setting",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Setting not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(ttl=60, prefix="user_setting_api")
def get_setting(
    key: str, current_user: TokenInfo = Depends(get_current_user)
) -> SettingResponse:
    """
    Get a specific setting by key.

    Returns the setting value and metadata.
    """
    settings_module = api.get_settings()
    if not settings_module:
        logger.error("Settings module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}},
        )

    try:
        # Use a targeted query instead of loading and scanning every setting
        setting = settings_module.get_one(current_user.user_id, key)

        if not setting:
            logger.warning(f"Setting '{key}' not found for user {current_user.user_id}")
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {"code": 404, "message": f"Setting '{key}' not found"}
                },
            )

        return SettingResponse(
            key=setting.key,
            value=setting.value,
            created_at=setting.created_at,
            updated_at=setting.updated_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to get setting '{key}' for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.put(
    "/{key}",
    response_model=SettingResponse,
    summary="Set a setting",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Limit exceeded or invalid key/value",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_setting(
    key: str, body: SettingValue, current_user: TokenInfo = Depends(get_current_user)
) -> SettingResponse:
    """
    Set a setting value.

    Creates or updates the setting with the given key.
    """
    # Special case for focused channel tracking (transient presence state).
    # On a malformed payload we must NOT silently store the raw JSON string as
    # a setting, so we surface the error instead of falling through.
    if key == "_focused_channel":
        presence_module = api.get_presence()
        if presence_module:
            try:
                data = json.loads(body.value)
                cid = data.get("channel_id")
                sid = data.get("server_id")

                presence_module.set_focused_channel(
                    current_user.user_id,
                    channel_id=int(cid) if cid and str(cid) != "0" else None,
                    server_id=int(sid) if sid and str(sid) != "0" else None,
                )
                return SettingResponse(
                    key=key,
                    value=body.value,
                    created_at=int(time.time() * 1000),
                    updated_at=int(time.time() * 1000),
                )
            except HTTPException:
                raise
            except Exception as e:
                logger.warning(f"Failed to update focused channel: {e}")
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "Invalid _focused_channel payload",
                        }
                    },
                )

    settings_module = api.get_settings()
    if not settings_module:
        logger.error("Settings module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}},
        )

    try:
        try:
            setting = settings_module.set_setting(current_user.user_id, key, body.value)
        except (
            SettingsLimitExceeded,
            SettingsKeyTooLong,
            SettingsValueTooLong,
            SettingsKeyReserved,
        ) as e:
            logger.warning(
                f"Failed to set setting '{key}' for user {current_user.user_id}: {e}"
            )
            raise HTTPException(
                status_code=400, detail={"error": {"code": 400, "message": str(e)}}
            )

        logger.info(f"Set setting '{key}' for user {current_user.user_id}")

        _invalidate_user_settings_cache(current_user.user_id)

        return SettingResponse(
            key=setting.key,
            value=setting.value,
            created_at=setting.created_at,
            updated_at=setting.updated_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to set setting '{key}' for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )


@router.delete(
    "/{key}",
    response_model=SuccessResponse,
    summary="Delete a setting",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Setting not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_setting(
    key: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Delete a setting.

    Removes the setting with the given key.
    """
    settings_module = api.get_settings()
    if not settings_module:
        logger.error("Settings module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}},
        )

    try:
        deleted = settings_module.delete_setting(current_user.user_id, key)

        if not deleted:
            logger.warning(
                f"Setting '{key}' not found for user {current_user.user_id} during delete"
            )
            raise HTTPException(
                status_code=404,
                detail={
                    "error": {"code": 404, "message": f"Setting '{key}' not found"}
                },
            )

        logger.info(f"Deleted setting '{key}' for user {current_user.user_id}")

        _invalidate_user_settings_cache(current_user.user_id)

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to delete setting '{key}' for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500, detail={"error": {"code": 500, "message": str(e)}}
        )
