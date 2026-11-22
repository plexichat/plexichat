"""
User Settings routes - Cloud-synced key-value store for user preferences.
"""

import json
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, Body, Path

import src.api as api
from src.core.database.cache.decorators import cached
from src.core.database.cache.operations import invalidate_cached, invalidate_pattern
from src.core.database.cache.serialization import generate_cache_key
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import authenticated_cache_key, require_user_token
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
    SettingsKeyInvalid,
    SettingsValueTooLong,
    SettingsKeyReserved,
    SettingsBulkLimitExceeded,
)
from src.core.security.hardening import parse_id

import utils.logger as logger

router = APIRouter(tags=["User Settings"])

# Server-semantics keys that the generic endpoints route to their dedicated
# handling instead of storing verbatim as user settings.
_SERVER_SETTING_KEYS = {"_focused_channel"}

# Setting keys whose dedicated read caches must be invalidated on write.
_PROFILE_VISIBILITY_KEY = "profile_visibility"


def _parse_focus_id(value, field: str):
    """Parse an optional focused channel/server id at the HTTP boundary.

    The ``_focused_channel`` payload is JSON supplied by the caller. Coercing
    its ids with a bare ``int()`` accepted Unicode decimal homoglyphs
    (``"٤٢"``/``"４２"`` -> ``42``), PEP-515 separators (``"1_0"`` -> ``10``),
    signs and values past the 63-bit snowflake ceiling. Those values are stored
    in Valkey and merged into the caller's presence payload as
    ``current_channel_id``/``current_server_id``, which is broadcast to other
    users and consumed by notification suppression. A parser differential
    between this write path and the snowflake-aware consumers means a client or
    a downstream service can read a different id than the one validated here.

    Reuse the shared ASCII-only, range-bounded gate so every consumer sees the
    same canonical positive snowflake (or ``None`` when focus is cleared).
    """
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if text == "" or text == "0":
            return None
    elif value == 0:
        # ``0`` is the documented "clear the focus" sentinel. bool is an int
        # subclass; ``False`` must not be silently accepted as a clear.
        if isinstance(value, bool):
            return parse_id(value, field)
        return None
    return parse_id(value, field)


def _invalidate_feature_caches(user_id: int, keys) -> None:
    """Invalidate feature-level caches derived from user settings.

    Currently covers the profile-privacy enforcement cache (short-TTL read
    of ``profile_visibility`` in the features/profiles route); unknown keys
    are ignored. Best-effort: never fails the write.
    """
    if not keys:
        return
    if _PROFILE_VISIBILITY_KEY in keys:
        try:
            from src.api.routes.features.profiles import (
                invalidate_profile_visibility_cache,
            )

            invalidate_profile_visibility_cache(int(user_id))
        except Exception as e:
            logger.debug(f"Profile visibility cache invalidation failed: {e}")


def _invalidate_user_settings_cache(
    current_user: TokenInfo, keys: Optional[list] = None
) -> None:
    """Invalidate all cached settings reads for a user (both list and per-key).

    Cache keys are produced by ``generate_cache_key`` from the route function
    arguments, so we reconstruct the exact keys here instead of relying on a
    glob pattern that can never line up with the generated key layout (the
    per-key key embeds ``str:<key>`` and yields a different colon count than any
    static pattern). This guarantees GET /settings and GET /{key} stop serving
    stale data after a write.

    The defensive fallback (unknown key set) uses a pattern anchored on this
    user's exact TokenInfo segment so it can never evict another user's
    entries, even if a legacy key contained colon-separated junk.
    """
    # Mirrors how the cached decorator builds keys when FastAPI invokes the
    # endpoints with keyword arguments (current_user=...).
    invalidate_cached(
        generate_cache_key("user_settings_all_api", current_user=current_user)
    )
    if keys:
        for key in keys:
            invalidate_cached(
                generate_cache_key(
                    "user_setting_api", key=key, current_user=current_user
                )
            )
    else:
        # Unknown key set (defensive) — wipe every per-key cache entry for THIS
        # user only. The wildcard tail spans just the key segment; the literal
        # ``TokenInfo:<uid>`` anchor prevents cross-user eviction.
        invalidate_pattern(
            f"user_setting_api:current_user:TokenInfo:{current_user.user_id}:key:*"
        )


@router.get(
    "",
    response_model=SettingsResponse,
    summary="Get all settings",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
@cached(
    ttl=60,
    prefix="user_settings_all_api",
    key_builder=authenticated_cache_key("user_settings_all_api"),
    l1_ttl=2,
)
def get_all_settings(
    current_user: TokenInfo = Depends(get_current_user),
) -> SettingsResponse:
    """
    Get all settings for the current user.

    Returns all key-value pairs stored for the user.
    """
    require_user_token(current_user)
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
    except Exception:
        logger.error(
            f"Failed to get settings for user {current_user.user_id}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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

    Accepts a dictionary of key-value pairs. The batch is capped (entry count
    and total payload size) and committed atomically: either every standard
    setting lands or none does.
    """
    require_user_token(current_user)
    settings_module = api.get_settings()
    presence_module = api.get_presence()

    if not settings_module:
        logger.error("Settings module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}},
        )

    if len(body) > settings_module.get_config().max_bulk_entries:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": (
                        f"Bulk update limited to "
                        f"{settings_module.config.max_bulk_entries} entries per request"
                    ),
                }
            },
        )

    serialized: Dict[str, str] = {}
    for key, raw_value in body.items():
        # Convert non-string values to JSON so they round-trip correctly
        # (a plain ``str()`` would mangle booleans/None/dicts, e.g.
        # ``True`` -> "True" and break later boolean comparisons).
        value = raw_value if isinstance(raw_value, str) else json.dumps(raw_value)
        serialized[key] = value

    total_bytes = sum(len(k) + len(v) for k, v in serialized.items())
    if total_bytes > settings_module.get_config().max_bulk_payload_bytes:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": (
                        f"Bulk update payload exceeds "
                        f"{settings_module.config.max_bulk_payload_bytes} bytes"
                    ),
                }
            },
        )

    failures: Dict[str, str] = {}
    updated = 0

    try:
        standard_items: Dict[str, str] = {}
        for key, value in serialized.items():
            # Server-semantics keys are routed to their dedicated handling;
            # they are persisted outside the standard batch transaction but
            # still counted accurately in the response.
            if key in _SERVER_SETTING_KEYS:
                if key == "_focused_channel" and presence_module:
                    try:
                        data = json.loads(value)
                        if not isinstance(data, dict):
                            raise ValueError(
                                "_focused_channel payload must be an object"
                            )
                        cid = _parse_focus_id(data.get("channel_id"), "channel_id")
                        sid = _parse_focus_id(data.get("server_id"), "server_id")

                        presence_module.set_focused_channel(
                            current_user.user_id,
                            channel_id=cid,
                            server_id=sid,
                        )
                        # Persist so GET /_focused_channel stays consistent with PUT.
                        settings_module.set_server_setting(
                            current_user.user_id, key, value
                        )
                        updated += 1
                    except Exception as e:
                        logger.warning(f"Failed to update focused channel: {e}")
                        failures[key] = "Failed to update focused channel"
                else:
                    failures[key] = (
                        "Reserved server setting cannot be written through the bulk endpoint"
                    )
                continue
            standard_items[key] = value

        if standard_items:
            try:
                results = settings_module.set_many(current_user.user_id, standard_items)
                updated += len(results)
            except (
                SettingsLimitExceeded,
                SettingsKeyTooLong,
                SettingsKeyInvalid,
                SettingsValueTooLong,
                SettingsKeyReserved,
                SettingsBulkLimitExceeded,
            ) as e:
                logger.warning(
                    f"Bulk update rejected for user {current_user.user_id}: {e}"
                )
                # Atomic: nothing from the standard batch was committed.
                for key in standard_items:
                    failures[key] = str(e)
            except Exception:
                logger.error(
                    f"Bulk update failed for user {current_user.user_id}",
                    exc_info=True,
                )
                for key in standard_items:
                    failures[key] = "Internal server error"

        _invalidate_user_settings_cache(current_user, keys=list(body.keys()))
        _invalidate_feature_caches(current_user.user_id, body.keys())

        return BulkSettingsResponse(
            success=len(failures) == 0 and updated == len(body),
            updated=updated,
            failed=len(failures),
            failures=failures,
        )
    except HTTPException:
        raise
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
# Partition the cache by credential, not just user id: a bot token carries its
# owner's ``user_id``, so the default key would let a warm owner entry be served
# to the bot without the handler body (and its guard) ever running.
@cached(
    ttl=60,
    prefix="user_setting_api",
    key_builder=authenticated_cache_key("user_setting_api"),
)
def get_setting(
    key: str, current_user: TokenInfo = Depends(get_current_user)
) -> SettingResponse:
    """
    Get a specific setting by key.

    Returns the setting value and metadata.
    """
    require_user_token(current_user)
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
    except Exception:
        logger.error(
            f"Failed to get setting '{key}' for user {current_user.user_id}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
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
    key: str = Path(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_.:-]+$"),
    body: SettingValue = Body(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> SettingResponse:
    """
    Set a setting value.

    Creates or updates the setting with the given key.
    """
    require_user_token(current_user)
    settings_module = api.get_settings()
    if not settings_module:
        logger.error("Settings module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}},
        )

    # Special case for focused channel tracking (transient presence state).
    # On a malformed payload we must NOT silently store the raw JSON string as
    # a setting, so we surface the error instead of falling through.
    if key == "_focused_channel":
        presence_module = api.get_presence()
        if presence_module:
            try:
                data = json.loads(body.value)
                if not isinstance(data, dict):
                    raise ValueError("_focused_channel payload must be an object")
                cid = _parse_focus_id(data.get("channel_id"), "channel_id")
                sid = _parse_focus_id(data.get("server_id"), "server_id")

                presence_module.set_focused_channel(
                    current_user.user_id,
                    channel_id=cid,
                    server_id=sid,
                )
                # Persist so GET /_focused_channel returns the same value.
                setting = settings_module.set_server_setting(
                    current_user.user_id, key, body.value
                )
                _invalidate_user_settings_cache(current_user, keys=[key])
                return SettingResponse(
                    key=setting.key,
                    value=setting.value,
                    created_at=setting.created_at,
                    updated_at=setting.updated_at,
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

    try:
        try:
            setting = settings_module.set_setting(current_user.user_id, key, body.value)
        except (
            SettingsLimitExceeded,
            SettingsKeyTooLong,
            SettingsKeyInvalid,
            SettingsValueTooLong,
            SettingsKeyReserved,
        ) as e:
            logger.warning(
                f"Failed to set setting '{key}' for user {current_user.user_id}: {e}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid request"}},
            )

        logger.info(f"Set setting '{key}' for user {current_user.user_id}")

        _invalidate_user_settings_cache(current_user, keys=[key])
        _invalidate_feature_caches(current_user.user_id, [key])

        return SettingResponse(
            key=setting.key,
            value=setting.value,
            created_at=setting.created_at,
            updated_at=setting.updated_at,
        )
    except HTTPException:
        raise
    except Exception:
        logger.error(
            f"Failed to set setting '{key}' for user {current_user.user_id}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{key}",
    response_model=SuccessResponse,
    summary="Delete a setting",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Reserved namespace key cannot be deleted",
        },
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

    Removes the setting with the given key. Reserved ``_``-prefixed
    (server-semantics) keys cannot be deleted through the generic
    endpoint, mirroring the write-side restriction.
    """
    require_user_token(current_user)
    settings_module = api.get_settings()
    if not settings_module:
        logger.error("Settings module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Settings module not available"}},
        )

    # Reserved namespace guard (consistent with the write side): `_`-prefixed
    # keys carry server semantics and are managed by their dedicated handlers,
    # never deleted via the generic endpoint.
    if key.startswith("_"):
        logger.warning(
            f"Rejected delete of reserved setting '{key}' for user "
            f"{current_user.user_id}"
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": (
                        f"Reserved setting '{key}' cannot be deleted through "
                        "the generic endpoint"
                    ),
                }
            },
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

        _invalidate_user_settings_cache(current_user, keys=[key])
        _invalidate_feature_caches(current_user.user_id, [key])

        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception:
        logger.error(
            f"Failed to delete setting '{key}' for user {current_user.user_id}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
