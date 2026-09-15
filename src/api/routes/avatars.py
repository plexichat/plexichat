"""
Avatar routes - Endpoints for user avatars and server icons.
"""

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
from src.api.schemas.avatars import AvatarUploadResponse, IconUploadResponse
from src.api.schemas.common import ErrorResponse, SuccessResponse
import utils.logger as logger

import hashlib
import threading
import time
from collections import OrderedDict
from typing import Optional

router = APIRouter(tags=["Avatars"])

# (M5) Bounded LRU + TTL checksum caches.
# Previously these were unbounded plain dicts: every unique user/server ID
# ever fetched stayed resident (slow memory leak / OOM risk via ID probing).
# Each cache is now an insertion-ordered OrderedDict capped at
# ``_MAX_CHECKSUM_CACHE_ENTRIES`` entries with per-entry TTL
# (``_CHECKSUM_CACHE_TTL_SEC``) and a threading.Lock. Reads refresh recency
# (true LRU), writes evict expired entries first then the oldest, so size
# never exceeds the cap. ``_cache_set_checksum`` also accepts plain dicts
# (legacy/test path) and applies the same cap there.
_MAX_CHECKSUM_CACHE_ENTRIES = 4096
_CHECKSUM_CACHE_TTL_SEC = 1800.0


class _BoundedChecksumCache:
    """Thread-safe bounded LRU cache with TTL for checksum strings. (M5)"""

    def __init__(
        self,
        maxsize: int = _MAX_CHECKSUM_CACHE_ENTRIES,
        ttl: float = _CHECKSUM_CACHE_TTL_SEC,
    ) -> None:
        self._maxsize = maxsize
        self._ttl = ttl
        self._data: "OrderedDict[int, tuple[str, float]]" = OrderedDict()
        self._lock = threading.Lock()

    def _expired(self, ts: float, now: float) -> bool:
        return (now - ts) > self._ttl

    def _purge_expired(self, now: float) -> None:
        # Oldest-first; stop at first live entry (timestamps are not
        # strictly ordered after LRU touch, so fall back to full scan
        # only when the cache is at/over capacity).
        for key in list(self._data.keys()):
            try:
                _, ts = self._data[key]
            except KeyError:
                continue
            if self._expired(ts, now):
                self._data.pop(key, None)
            if len(self._data) <= self._maxsize:
                # Still scan the head for staleness when small; cheap.
                pass
        # Enforce hard cap (evict oldest first).
        while len(self._data) > self._maxsize:
            self._data.popitem(last=False)

    def get(self, key: int, default: Optional[str] = None) -> Optional[str]:
        now = time.monotonic()
        with self._lock:
            try:
                value, ts = self._data[key]
            except KeyError:
                return default
            if self._expired(ts, now):
                self._data.pop(key, None)
                return default
            self._data.move_to_end(key)
            return value

    def __getitem__(self, key: int) -> Optional[str]:
        result = self.get(key)
        if result is None:
            # Distinguish missing from stored None (we never store None).
            with self._lock:
                if key not in self._data:
                    raise KeyError(key)
            return result
        return result

    def __setitem__(self, key: int, value: str) -> None:
        now = time.monotonic()
        with self._lock:
            if key in self._data:
                self._data.pop(key, None)
            self._data[key] = (value, now)
            if len(self._data) > self._maxsize:
                self._purge_expired(now)
                while len(self._data) > self._maxsize:
                    self._data.popitem(last=False)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, int):
            return False
        now = time.monotonic()
        with self._lock:
            try:
                _, ts = self._data[key]
            except KeyError:
                return False
            if self._expired(ts, now):
                self._data.pop(key, None)
                return False
            return True

    def pop(self, key: int, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            entry = self._data.pop(key, None)
        if entry is None:
            return default
        return entry[0]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        now = time.monotonic()
        with self._lock:
            self._purge_expired(now)
            return len(self._data)


_avatar_checksum_cache = _BoundedChecksumCache()  # {uid: checksum}
_icon_checksum_cache = _BoundedChecksumCache()  # {sid: checksum}


def _cache_set_checksum(cache, key: int, checksum: str) -> None:
    """Insert into a checksum cache, evicting the oldest entry at the cap."""
    if isinstance(cache, _BoundedChecksumCache):
        cache[key] = checksum
        return
    # Legacy plain-dict path (kept for backwards compatibility / tests).
    if len(cache) >= _MAX_CHECKSUM_CACHE_ENTRIES:
        # Evict the first-inserted entry (dicts preserve insertion order).
        try:
            oldest = next(iter(cache))
            cache.pop(oldest, None)
        except StopIteration:
            pass
    cache[key] = checksum


def _invalidate_user_avatar_cache(user_id: int) -> None:
    """Drop all cached avatar state so a re-upload/deletion is reflected.

    The route keeps its own in-process checksum cache (``_avatar_checksum_cache``)
    which is never refreshed by the manager, and the manager caches the ETag
    checksum in Valkey under ``user_avatar_checksum:{user_id}``. Both must be
    cleared or clients keep receiving ``304 Not Modified`` for the old avatar.
    """
    _avatar_checksum_cache.pop(user_id, None)
    try:
        from src.core.database import invalidate_pattern

        invalidate_pattern(f"user_avatar_checksum:{user_id}")
    except Exception as e:  # pragma: no cover - cache invalidation is best-effort
        logger.debug(
            f"Failed to invalidate user avatar checksum cache for {user_id}: {e}"
        )


def _invalidate_server_icon_cache(server_id: int) -> None:
    """Drop all cached server-icon state so a re-upload/deletion is reflected."""
    _icon_checksum_cache.pop(server_id, None)

    try:
        from src.core.database import invalidate_pattern

        invalidate_pattern(f"server_icon_checksum:{server_id}")
    except Exception as e:  # pragma: no cover - cache invalidation is best-effort
        logger.debug(
            f"Failed to invalidate server icon checksum cache for {server_id}: {e}"
        )


@router.get(
    "/users/{user_id:int}",
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
async def get_user_avatar(user_id: int, request: Request):
    """
    Get user avatar image.

    Returns the avatar image bytes with appropriate content type.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    try:
        uid = user_id

        # 1. Fast check for ETag
        etag_client = request.headers.get("If-None-Match")

        # Check local RAM cache for checksum first
        checksum = _avatar_checksum_cache.get(uid)
        if not checksum:
            try:
                checksum = avatars.get_user_avatar_checksum(uid)
                if checksum:
                    _cache_set_checksum(_avatar_checksum_cache, uid, checksum)
            except Exception:
                pass

        # Flexible ETag check (handles weak tags and quotes)
        if checksum and etag_client:
            # Check for exact match, quoted match, or weak match.
            # NOTE: only an exact match is accepted - a substring check
            # (``checksum in etag_client``) previously let any client ETag
            # containing the checksum substring return 304.
            if (
                etag_client == checksum
                or etag_client == f'"{checksum}"'
                or etag_client == f'W/"{checksum}"'
            ):
                return Response(status_code=304)

        # 2. Fetch full data (no threadpool - the module handles caching internally)
        try:
            result = avatars.get_user_avatar_data(uid)
            if result is not None:
                # SECURITY (Agent7 L1): defense in depth - even if a poisoned
                # row bypasses the manager guard, never serve a script-carrying
                # SVG raw. Fall through to the safe generated default below.
                from src.core.avatars.manager.svg_guard import (
                    is_unsafe_serve_payload,
                )

                _payload, _ctype, _ = result
                if is_unsafe_serve_payload(_ctype, _payload):
                    logger.warning(
                        f"Blocked unsafe stored avatar serve for user {uid} "
                        f"(type={_ctype})"
                    )
                    result = None
            if not result:
                # Generate a default SVG avatar if none exists
                from src.core.avatars import generate_default_svg

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
                except Exception:
                    pass

                svg_content = generate_default_svg(username or str(uid), initials)
                # Ensure content is encoded before hashing
                svg_bytes = (
                    svg_content.encode()
                    if isinstance(svg_content, str)
                    else svg_content
                )
                svg_etag = hashlib.sha256(svg_bytes).hexdigest()

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
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    # Validate file type
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
        file_data = await file.read()
    except Exception as e:
        logger.error(
            f"Failed to read avatar file upload from user {current_user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": 400, "message": f"Failed to read file: {str(e)}"}
            },
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
                    "id": current_user.user_id,
                    "username": "",
                    "avatar_url": result["url"],
                },
                exclude_user_id=current_user.user_id,
            )
        except Exception as ge:
            logger.debug(f"emit_user_update (avatar upload) failed: {ge}")

        return AvatarUploadResponse(
            success=True,
            avatar_url=result["url"],
            width=result["width"],
            height=result["height"],
            size=result["size"],
            animated=result["animated"],
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
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

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
                    "id": current_user.user_id,
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
async def get_server_icon(server_id: int, request: Request):
    """
    Get server icon image.

    Returns the icon image bytes with appropriate content type.
    """
    avatars = api.get_avatars()
    if not avatars:
        logger.error("Avatars module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    try:
        sid = server_id

        # 1. Fast check for ETag
        etag_client = request.headers.get("If-None-Match")

        # Check local RAM cache for checksum first
        checksum = _icon_checksum_cache.get(sid)
        if not checksum:
            try:
                checksum = avatars.get_server_icon_checksum(sid)
                if checksum:
                    _cache_set_checksum(_icon_checksum_cache, sid, checksum)
            except Exception:
                pass

        if checksum and etag_client == f'"{checksum}"':
            return Response(status_code=304)

        # 2. Fetch full data
        try:
            result = avatars.get_server_icon_data(sid)
            if result is not None:
                # SECURITY (Agent7 L1): defense in depth - even if a poisoned
                # row bypasses the manager guard, never serve a script-carrying
                # SVG raw. Fall through to the safe generated default below.
                from src.core.avatars.manager.svg_guard import (
                    is_unsafe_serve_payload,
                )

                _payload, _ctype, _ = result
                if is_unsafe_serve_payload(_ctype, _payload):
                    logger.warning(
                        f"Blocked unsafe stored server icon serve {sid} (type={_ctype})"
                    )
                    result = None
            if not result:
                # Generate a default SVG icon if none exists
                from src.core.avatars import generate_default_svg

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
                                # Get first character of first two words, or just first two chars
                                words = name.split()
                                if len(words) >= 2:
                                    initials = (words[0][0] + words[1][0]).upper()
                                else:
                                    initials = name[: min(2, len(name))].upper()
                except Exception as e:
                    logger.debug(f"Failed to get server name for initials: {e}")

                try:
                    svg_content = generate_default_svg(sid, initials)
                    svg_bytes = (
                        svg_content.encode()
                        if isinstance(svg_content, str)
                        else svg_content
                    )
                    svg_etag = hashlib.sha256(svg_bytes).hexdigest()

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
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    if not servers:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
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
            file_data = await file.read()
        except Exception as e:
            logger.error(
                f"Failed to read icon file upload for server {sid} from user {current_user.user_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {"code": 400, "message": f"Failed to read file: {str(e)}"}
                },
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

        return IconUploadResponse(
            success=True,
            icon_url=result["url"],
            width=result["width"],
            height=result["height"],
            size=result["size"],
            animated=result["animated"],
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
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Avatars module not available"}},
        )

    if not servers:
        logger.error("Servers module not available")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    try:
        try:
            sid = int(server_id)
        except (ValueError, TypeError):
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
