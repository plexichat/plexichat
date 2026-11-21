"""
Avatar route handlers - Endpoints for user avatars and server icons.

Split from the monolithic ``avatars.py`` into single-responsibility modules.
This module owns the HTTP layer (authz, upload, serve, delete) and delegates
validation to :mod:`validation`, caching/storage to :mod:`storage`, and typed
errors to :mod:`errors`.

All public handler names and module-level objects (``router``, the checksum
caches, the invalidation helpers) are re-exported by ``__init__.py`` so the
package remains a drop-in replacement for the old ``src.api.routes.avatars``
module.
"""

from __future__ import annotations
from src.api.request_guards import MAX_AVATAR_UPLOAD, read_upload_capped

from src.core.avatars.manager.upload_guard import (
    content_disposition,
    extension_for_content_type,
)

import hashlib
from typing import Optional

from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
    File,
    UploadFile,
    Request,
    Response,
)

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.avatars import AvatarUploadResponse, IconUploadResponse
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeIDParam,
    SuccessResponse,
)
import utils.logger as logger

from .errors import AvatarValidationError
from .models import upload_result_from_manager
from .storage import (
    _cache_set_checksum,
    _avatar_checksum_cache,
    _icon_checksum_cache,
    _avatar_absent_cache,
    _icon_absent_cache,
    _user_initials_cache,
    _server_initials_cache,
    _default_svg_cache,
    _icon_payload_cache,
    _invalidate_user_avatar_cache,
    _invalidate_server_icon_cache,
)
from .validation import (
    is_serve_safe,
    parse_server_id,
    validate_avatar_upload,
)

router = APIRouter(tags=["Avatars"])


def _not_available() -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={"error": {"code": 500, "message": "Avatars module not available"}},
    )


def _default_svg(seed: object, initials: str) -> tuple[str, str]:
    """Return ``(svg_markup, sha256_etag)`` for a default avatar.

    The template only depends on ``(seed, initials)``; the bounded cache keeps
    repeat requests from re-entering the manager and re-hashing the markup.
    """
    key = (str(seed), str(initials))
    cached = _default_svg_cache.get(key)
    if cached is not None:
        return cached
    from src.core.avatars import generate_default_svg

    svg_content = generate_default_svg(seed, initials)
    svg_bytes = svg_content.encode() if isinstance(svg_content, str) else svg_content
    etag = hashlib.sha256(svg_bytes).hexdigest()
    result = (svg_content, etag)
    _default_svg_cache.set(key, result)
    return result


@router.get(
    "/users/{user_id}",
    summary="Get user avatar",
    responses={
        200: {
            "description": "The user avatar image file",
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "image/gif": {},
                "image/webp": {},
                "image/svg+xml": {},
            },
        },
        400: {"model": ErrorResponse, "description": "Invalid user ID"},
        404: {"model": ErrorResponse, "description": "Avatar not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_avatar(user_id: SnowflakeIDParam, request: Request):
    """
    Get user avatar image.

    Returns the avatar image bytes with appropriate content type.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise _not_available()

    try:
        uid = user_id

        # 1. Fast check for ETag. A cached "no avatar" verdict skips both the
        # checksum and the binary lookup (each a DB round-trip on a miss).
        etag_client = request.headers.get("If-None-Match")
        avatar_absent = bool(_avatar_absent_cache.get(uid))

        checksum = None
        if not avatar_absent:
            # Check local RAM cache for checksum first
            checksum = _avatar_checksum_cache.get(uid)
            if not checksum:
                try:
                    checksum = avatars.get_user_avatar_checksum(uid)
                    if checksum:
                        _cache_set_checksum(_avatar_checksum_cache, uid, checksum)
                except Exception as cache_error:
                    # The avatar is still served; only the ETag cache misses.
                    logger.debug(f"avatar checksum cache store failed: {cache_error}")

            # Flexible ETag check (handles weak tags and quotes). Only an exact
            # match is accepted - a substring check previously let any client ETag
            # containing the checksum substring return 304.
            if checksum and etag_client:
                if (
                    etag_client == checksum
                    or etag_client == f'"{checksum}"'
                    or etag_client == f'W/"{checksum}"'
                ):
                    return Response(status_code=304)

        # 2. Fetch full data (no threadpool - the module handles caching internally)
        try:
            result = None if avatar_absent else avatars.get_user_avatar_data(uid)
            if result is not None:
                # SECURITY: defense in depth - never serve a script-carrying
                # SVG raw, even if a poisoned row bypasses the manager guard.
                _payload, _ctype, _avatar_checksum = result
                if not is_serve_safe(_ctype, _payload, _avatar_checksum):
                    logger.warning(
                        "Blocked unsafe stored avatar serve for user "
                        f"{uid} (type={_ctype})"
                    )
                    result = None
            if not result:
                # Remember the negative lookup so repeat requests skip both
                # the checksum and the binary read for this user.
                _cache_set_checksum(_avatar_absent_cache, uid, "1")
                # Generate a default SVG avatar if none exists
                cached_initials = _user_initials_cache.get(uid)
                if cached_initials is not None:
                    cached_username, initials = cached_initials.split("|", 1)
                    username = cached_username or None
                else:
                    # Try to get username for initials (match frontend logic)
                    username = None
                    initials = "UC"
                    try:
                        auth_mod = api.get_auth()
                        if auth_mod:
                            profiles = auth_mod.get_user_profiles_bulk([uid])
                            user = profiles.get(str(uid)) if profiles else None
                            if user:
                                username = getattr(user, "get", None)
                                username = (
                                    user.get("username", "User")
                                    if callable(username)
                                    else getattr(user, "username", "User")
                                ).strip()
                                if username:
                                    initials = username[: min(2, len(username))].upper()
                    except Exception as initial_error:
                        logger.debug(
                            f"avatar initials fallback failed: {initial_error}"
                        )
                    _cache_set_checksum(
                        _user_initials_cache, uid, f"{username or ''}|{initials}"
                    )

                svg_content, svg_etag = _default_svg(username or str(uid), initials)

                if etag_client == f'"{svg_etag}"':
                    return Response(status_code=304)

                return Response(
                    content=svg_content,
                    media_type="image/svg+xml",
                    headers={
                        "Cache-Control": "public, max-age=3600",
                        "ETag": f'"{svg_etag}"',
                        "Access-Control-Allow-Origin": "*",
                        "Cross-Origin-Resource-Policy": "cross-origin",
                        "X-Content-Type-Options": "nosniff",
                        "Content-Disposition": content_disposition(
                            "avatar.svg", disposition="inline"
                        ),
                    },
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to fetch avatar for user {user_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch avatar"}},
            )

        avatar_data, content_type, checksum = result

        # Update local checksum cache
        if checksum:
            _cache_set_checksum(_avatar_checksum_cache, uid, checksum)

        return Response(
            content=avatar_data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "ETag": f'"{checksum}"',
                "Content-Length": str(len(avatar_data)),
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Cross-Origin-Resource-Policy": "cross-origin",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": content_disposition(
                    f"avatar.{extension_for_content_type(content_type)}",
                    disposition="inline",
                ),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_user_avatar for user {user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/users/@me",
    response_model=AvatarUploadResponse,
    summary="Upload my avatar",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file type or read error"},
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_my_avatar(
    file: UploadFile = File(...), current_user: TokenInfo = Depends(get_current_user)
) -> AvatarUploadResponse:
    """
    Upload current user's avatar.

    Accepts image file upload and stores it in the database.
    """
    require_user_token(current_user)
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise _not_available()

    # Validate file type (quick reject before reading the body).
    if not file.content_type or not file.content_type.startswith("image/"):
        logger.warning(
            f"User {current_user.user_id} attempted to upload non-image file: {file.content_type}"
        )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "File must be an image"}},
        )

    # Read file data
    try:
        file_data = await read_upload_capped(file, MAX_AVATAR_UPLOAD)
    except Exception as e:
        logger.error(
            f"Failed to read avatar file upload from user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=400,
            # Fail closed: the raw exception detail (which can embed an
            # absolute filesystem path) was logged above with exc_info and
            # must never be echoed to the client (CWE-209).
            detail={"error": {"code": 400, "message": "Failed to read file"}},
        )

    # Hardened validation: allowed MIME, magic-byte/content-type verification,
    # size limit. Fail closed; never leak exception text.
    try:
        validate_avatar_upload(file.content_type, file_data)
    except AvatarValidationError:
        logger.warning(
            f"User {current_user.user_id} uploaded a rejected avatar file "
            f"(type={file.content_type}, size={len(file_data)})"
        )
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "File must be an image"}},
        )

    try:
        try:
            result = avatars.upload_user_avatar(
                user_id=current_user.user_id,
                image_data=file_data,
                content_type=file.content_type,
            )
        except (ValueError, TypeError):
            # M1: invalid image data is a CLIENT error - report it as such
            # instead of a misleading "Internal server error" 400.
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid image file"}},
            )
        except Exception as e:
            logger.error(
                f"Failed to upload avatar for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Upload failed"}},
            )

        # Invalidate user cache
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern(f"user:*{current_user.user_id}*")
        except Exception as e:
            logger.debug(
                f"Failed to invalidate cache for user {current_user.user_id} after avatar upload: {e}"
            )
        _invalidate_user_avatar_cache(current_user.user_id)

        # H2 parity: the composer path (POST /users/@me/avatar) emits
        # USER_UPDATE so open sessions refresh; this sibling endpoint must
        # behave identically instead of leaving stale avatars everywhere.
        try:
            from src.core.events.gateway_emit import emit_user_update

            emit_user_update(
                {
                    "id": str(current_user.user_id),
                    "username": "",
                    "avatar_url": result["url"],
                },
                exclude_user_id=current_user.user_id,
            )
        except Exception as ge:
            logger.debug(f"emit_user_update (avatar upload) failed: {ge}")

        upload_result = upload_result_from_manager(result)
        return AvatarUploadResponse(
            success=upload_result.success,
            avatar_url=upload_result.url,
            width=upload_result.width,
            height=upload_result.height,
            size=upload_result.size,
            animated=upload_result.animated,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in upload_my_avatar for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/users/@me",
    response_model=SuccessResponse,
    summary="Delete my avatar",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_my_avatar(
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """
    Delete current user's avatar.
    """
    require_user_token(current_user)
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise _not_available()

    try:
        try:
            deleted = avatars.delete_user_avatar(current_user.user_id)
        except Exception as e:
            logger.error(
                f"Failed to delete avatar for user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to delete avatar"}},
            )

        # Invalidate user cache
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern(f"user:*{current_user.user_id}*")
        except Exception as e:
            logger.debug(
                f"Failed to invalidate cache for user {current_user.user_id} after avatar deletion: {e}"
            )
        _invalidate_user_avatar_cache(current_user.user_id)

        # H2 parity: connected sessions must learn the avatar was removed.
        try:
            from src.core.events.gateway_emit import emit_user_update

            emit_user_update(
                {
                    "id": str(current_user.user_id),
                    "username": "",
                    "avatar_url": "",
                },
                exclude_user_id=current_user.user_id,
            )
        except Exception as ge:
            logger.debug(f"emit_user_update (avatar delete) failed: {ge}")

        return SuccessResponse(success=deleted, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_my_avatar for user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# NOTE (M5): _icon_checksum_cache is defined once at module top as a
# _BoundedChecksumCache; no second definition here (it would rebind to an
# unbounded plain dict and wipe the cache).


@router.get(
    "/servers/{server_id}",
    summary="Get server icon",
    responses={
        200: {
            "description": "The server icon image file",
            "content": {
                "image/png": {},
                "image/jpeg": {},
                "image/gif": {},
                "image/webp": {},
                "image/svg+xml": {},
            },
        },
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        404: {"model": ErrorResponse, "description": "Icon not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_server_icon(server_id: SnowflakeIDParam, request: Request):
    """
    Get server icon image.

    Returns the icon image bytes with appropriate content type.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise _not_available()

    try:
        sid = server_id

        # 1. Fast check for ETag. A cached "no icon" verdict skips both the
        # checksum and the binary lookup (each a DB round-trip on a miss).
        etag_client = request.headers.get("If-None-Match")
        icon_absent = bool(_icon_absent_cache.get(sid))

        checksum = None
        if not icon_absent:
            # Check local RAM cache for checksum first
            checksum = _icon_checksum_cache.get(sid)
            if not checksum:
                try:
                    checksum = avatars.get_server_icon_checksum(sid)
                    if checksum:
                        _cache_set_checksum(_icon_checksum_cache, sid, checksum)
                except Exception as cache_error:
                    logger.debug(
                        f"server icon checksum cache store failed: {cache_error}"
                    )

            if checksum and etag_client == f'"{checksum}"':
                return Response(status_code=304)

        # 2. Fetch full data (resolved-payload cache first). An entry is only
        # stored after the serve-safety verdict below has passed, and uploads/
        # deletions invalidate it, so a warm hit skips the manager's checksum
        # and binary DB/Valkey lookups without changing the bytes served.
        try:
            result = None if icon_absent else _icon_payload_cache.get(sid)
            if result is None and not icon_absent:
                result = avatars.get_server_icon_data(sid)
                if result is not None:
                    # SECURITY: defense in depth - never serve a script-carrying
                    # SVG raw. Only validated payloads enter the resolved cache.
                    _payload, _ctype, _avatar_checksum = result
                    if is_serve_safe(_ctype, _payload, _avatar_checksum):
                        _icon_payload_cache[sid] = result
                    else:
                        logger.warning(
                            f"Blocked unsafe stored server icon serve {sid} (type={_ctype})"
                        )
                        result = None
            if not result:
                # Remember the negative lookup so repeat requests skip both
                # the checksum and the binary read for this server.
                _cache_set_checksum(_icon_absent_cache, sid, "1")
                # Generate a default SVG icon if none exists
                initials = _server_initials_cache.get(sid)
                if initials is None:
                    # Try to get server name for initials
                    initials = "S"
                    try:
                        db = api.get_db()
                        if db:
                            row = db.fetch_one(
                                "SELECT name FROM srv_servers WHERE id = ?", (sid,)
                            )
                            if row and row.get("name"):
                                name = str(row["name"]).strip()
                                if name:
                                    # First characters of the first two words,
                                    # or just the first two characters.
                                    words = name.split()
                                    if len(words) >= 2:
                                        initials = (words[0][0] + words[1][0]).upper()
                                    else:
                                        initials = name[: min(2, len(name))].upper()
                    except Exception as e:
                        logger.debug(f"Failed to get server name for initials: {e}")
                    _cache_set_checksum(_server_initials_cache, sid, initials)

                try:
                    svg_content, svg_etag = _default_svg(sid, initials)

                    if etag_client == f'"{svg_etag}"':
                        return Response(status_code=304)

                    return Response(
                        content=svg_content,
                        media_type="image/svg+xml",
                        headers={
                            "Cache-Control": "public, max-age=3600",
                            "ETag": f'"{svg_etag}"',
                            "Access-Control-Allow-Origin": "*",
                            "Cross-Origin-Resource-Policy": "cross-origin",
                            "X-Content-Type-Options": "nosniff",
                            "Content-Disposition": content_disposition(
                                "icon.svg", disposition="inline"
                            ),
                        },
                    )
                except Exception as e:
                    logger.error(
                        f"Failed to generate default SVG for server {sid}: {e}"
                    )
                    raise HTTPException(status_code=404, detail="Icon not found")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Failed to fetch icon for server {server_id}: {e}", exc_info=True
            )
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Failed to fetch icon"}},
            )

        icon_data, content_type, checksum = result

        # Update local checksum cache
        if checksum:
            _cache_set_checksum(_icon_checksum_cache, sid, checksum)

        return Response(
            content=icon_data,
            media_type=content_type,
            headers={
                "Cache-Control": "public, max-age=86400",
                "ETag": f'"{checksum}"',
                "Content-Length": str(len(icon_data)),
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "GET, OPTIONS",
                "Cross-Origin-Resource-Policy": "cross-origin",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": content_disposition(
                    f"icon.{extension_for_content_type(content_type)}",
                    disposition="inline",
                ),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in get_server_icon for server {server_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/servers/{server_id}",
    response_model=IconUploadResponse,
    summary="Upload server icon",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file type or read error"},
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_server_icon(
    server_id: str,
    file: UploadFile = File(...),
    current_user: TokenInfo = Depends(get_current_user),
) -> IconUploadResponse:
    """
    Upload server icon.

    Requires server ownership or MANAGE_SERVER permission.
    """
    avatars = api.get_avatars()
    servers = api.get_servers()
    sid: Optional[int] = None

    if not avatars:
        logger.error("Avatars module not available")
        raise _not_available()

    if not servers:
        logger.error("Servers module not available")
        raise _not_available()

    try:
        try:
            sid = parse_server_id(server_id)
        except AvatarValidationError:
            logger.warning(
                f"User {current_user.user_id} provided invalid server ID for icon upload: {server_id}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Check permission
        try:
            server = servers.get_server(sid, current_user.user_id)
            if not server:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Server not found"}},
                )

            # Check if user is owner or has permission
            is_owner = server.owner_id == current_user.user_id
            has_permission = False

            if not is_owner:
                member = servers.get_member(sid, current_user.user_id)
                if member:
                    # Check for server.manage permission
                    has_permission = servers.has_permission(
                        current_user.user_id, sid, "server.manage"
                    )

            if not is_owner and not has_permission:
                logger.warning(
                    f"User {current_user.user_id} denied MANAGE_SERVER for server {sid}"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Permission denied"}},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error checking permissions for server {sid} icon upload: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Failed to verify permissions"}
                },
            )

        # Validate file type
        if not file.content_type or not file.content_type.startswith("image/"):
            logger.warning(
                f"User {current_user.user_id} attempted to upload non-image icon for server {sid}: {file.content_type}"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "File must be an image"}},
            )

        # Read file data
        try:
            file_data = await read_upload_capped(file, MAX_AVATAR_UPLOAD)
        except Exception as e:
            logger.error(
                f"Failed to read icon file upload for server {sid} from user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=400,
                # Fail closed: the raw exception detail (which can embed an
                # absolute filesystem path) was logged above with exc_info and
                # must never be echoed to the client (CWE-209).
                detail={"error": {"code": 400, "message": "Failed to read file"}},
            )

        # Hardened validation: allowed MIME, magic-byte/content-type verification,
        # size limit. Fail closed; never leak exception text.
        try:
            validate_avatar_upload(file.content_type, file_data)
        except AvatarValidationError:
            logger.warning(
                f"User {current_user.user_id} uploaded a rejected icon for server {sid} "
                f"(type={file.content_type}, size={len(file_data)})"
            )
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "File must be an image"}},
            )

        try:
            result = avatars.upload_server_icon(
                server_id=sid, image_data=file_data, content_type=file.content_type
            )
        except (ValueError, TypeError):
            # M1: invalid image data is a CLIENT error - report it as such
            # instead of a misleading "Internal server error" 400.
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid image file"}},
            )
        except Exception as e:
            logger.error(f"Failed to upload icon for server {sid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={"error": {"code": 500, "message": "Upload failed"}},
            )

        # H2: propagate the new icon to connected clients immediately so
        # every open session reflects the change (no stale icons until
        # re-fetch). The sibling /servers/{id}/icon endpoint emits too.
        try:
            from src.core.events.gateway_emit import emit_guild_update

            emit_guild_update(sid, icon=result["url"])
        except Exception as ge:
            logger.debug(f"emit_guild_update (icon upload) failed: {ge}")

        upload_result = upload_result_from_manager(result)
        return IconUploadResponse(
            success=upload_result.success,
            icon_url=upload_result.url,
            width=upload_result.width,
            height=upload_result.height,
            size=upload_result.size,
            animated=upload_result.animated,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in upload_server_icon for server {server_id} by user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
    finally:
        # Always drop cached icon state so a re-upload is reflected.
        if sid is not None:
            _invalidate_server_icon_cache(sid)


@router.delete(
    "/servers/{server_id}",
    response_model=SuccessResponse,
    summary="Delete server icon",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid user token"},
        403: {"model": ErrorResponse, "description": "Permission denied"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_server_icon(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """
    Delete server icon.

    Requires server ownership or MANAGE_SERVER permission.
    """
    avatars = api.get_avatars()
    servers = api.get_servers()

    if not avatars:
        logger.error("Avatars module not available")
        raise _not_available()

    if not servers:
        logger.error("Servers module not available")
        raise _not_available()

    try:
        try:
            sid = parse_server_id(server_id)
        except AvatarValidationError:
            logger.warning(f"Invalid server ID format for icon deletion: {server_id}")
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )

        # Check permission
        try:
            server = servers.get_server(sid, current_user.user_id)
            if not server:
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Server not found"}},
                )

            is_owner = server.owner_id == current_user.user_id
            has_permission = False

            if not is_owner:
                member = servers.get_member(sid, current_user.user_id)
                if member:
                    has_permission = servers.has_permission(
                        current_user.user_id, sid, "server.manage"
                    )

            if not is_owner and not has_permission:
                logger.warning(
                    f"User {current_user.user_id} denied MANAGE_SERVER for server {sid} icon deletion"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Permission denied"}},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Error checking permissions for server {sid} icon deletion: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Failed to verify permissions"}
                },
            )

        try:
            deleted = avatars.delete_server_icon(sid)
        except Exception as e:
            logger.error(f"Failed to delete icon for server {sid}: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": f"Deletion failed: {str(e)}"}
                },
            )

        # Invalidate server cache
        try:
            from src.core.database import invalidate_pattern

            invalidate_pattern(f"server:*{sid}*")
        except Exception as e:
            logger.debug(
                f"Failed to invalidate cache for server {sid} after icon deletion: {e}"
            )
        _invalidate_server_icon_cache(sid)

        # H2: icon removal must also reach connected clients immediately,
        # otherwise sessions keep showing the deleted icon until re-fetch.
        try:
            from src.core.events.gateway_emit import emit_guild_update

            emit_guild_update(sid, icon="")
        except Exception as ge:
            logger.debug(f"emit_guild_update (icon delete) failed: {ge}")

        return SuccessResponse(success=deleted, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Unexpected error in delete_server_icon for server {server_id} by user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


__all__ = [
    "router",
    "get_user_avatar",
    "upload_my_avatar",
    "delete_my_avatar",
    "get_server_icon",
    "upload_server_icon",
    "delete_server_icon",
    "_not_available",
]
