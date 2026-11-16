"""
Sticker routes - Sticker and pack management endpoints.
"""

from typing import List, NoReturn, Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query

import time

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.stickers import StickerResponse, StickerPackResponse
from src.api.schemas.common import ErrorResponse
from src.core.stickers import PackType
from src.core.stickers.exceptions import (
    InvalidStickerFormatError,
    StickerTooLargeError,
    InvalidStickerNameError,
    StickerLimitError,
    PackLimitError,
    PackNotFoundError,
    PermissionDeniedError,
    StickerNotFoundError,
)
from src.core import ratelimit
from fastapi import Request
from src.api.schemas.common import _reject_ui_text_chars

# Hard ceiling applied at the HTTP boundary so a single request cannot buffer
# an unbounded body into memory before the manager's own size check runs.
STICKER_MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB

# Expected, user-facing failures from the sticker manager. These carry safe
# messages worth returning; everything else is collapsed to a generic 500 so
# internal state (SQL, file paths) never leaks to clients.
_STICKER_CLIENT_ERRORS = (
    InvalidStickerFormatError,
    StickerTooLargeError,
    InvalidStickerNameError,
    StickerLimitError,
    PackLimitError,
    PackNotFoundError,
)

router = APIRouter(tags=["Stickers"])

#: Bounds on the raw comma-separated tag field and the suggestion query.
MAX_STICKER_TAG_PAYLOAD = 400
MAX_STICKER_SUGGESTION_QUERY = 256

# BEFORE: hydrating a server's sticker packs called ``get_pack_stickers`` once
# per pack. Each call re-ran ``get_pack`` (JOIN + membership probe) and issued
# its own sticker query, so a server with N packs cost 1 + 3N round trips.
# AFTER: one batched ``sticker_stickers`` query resolves every pack at once;
# the original per-pack path is kept as a fallback when the batch path cannot
# run (module not wired, unexpected schema). Membership was already validated
# by ``get_server_packs`` before this helper is called.
_MAX_STICKER_BATCH_CHUNK = 400


def _batch_pack_stickers(stickers_mod, packs, user_id: int) -> dict:
    """Return ``{pack_id: [Sticker, ...]}`` for all ``packs`` in one query."""
    by_pack: dict = {int(pack.id): [] for pack in packs}
    if not by_pack:
        return by_pack

    manager = None
    db = None
    try:
        manager = stickers_mod._get_manager()
        db = getattr(manager, "_db", None)
    except Exception:
        db = None

    if (
        db is not None
        and hasattr(db, "fetch_all")
        and hasattr(manager, "_row_to_sticker")
    ):
        pack_ids = list(by_pack)
        try:
            for start in range(0, len(pack_ids), _MAX_STICKER_BATCH_CHUNK):
                chunk = pack_ids[start : start + _MAX_STICKER_BATCH_CHUNK]
                placeholders = ",".join("?" * len(chunk))
                rows = db.fetch_all(
                    "SELECT s.*, COUNT(u.id) as usage_count "
                    "FROM sticker_stickers s "
                    "LEFT JOIN sticker_usage u ON s.id = u.sticker_id "
                    f"WHERE s.pack_id IN ({placeholders}) "
                    "GROUP BY s.id ORDER BY s.pack_id, s.name",
                    tuple(chunk),
                )
                for row in rows:
                    pid = int(row["pack_id"])
                    if pid in by_pack:
                        by_pack[pid].append(manager._row_to_sticker(row))
            return by_pack
        except Exception as e:
            logger.debug(f"batch pack sticker lookup failed: {e}")

    # Fallback: preserve the original per-pack semantics exactly.
    for pack in packs:
        try:
            by_pack[int(pack.id)] = stickers_mod.get_pack_stickers(user_id, pack.id)
        except Exception as e:
            logger.debug(f"per-pack sticker lookup failed for {pack.id}: {e}")
    return by_pack


def _build_shared_ratelimit_storage():
    """Build the shared/persistent storage backend used at startup.

    Mirrors ``src.server.initializer`` preference order: Valkey
    (distributed) first, then the shared relational database. Returns
    ``None`` when no shared backend is available so the caller can fail
    closed instead of silently downgrading to per-process memory (F7).
    """
    try:
        from src.core.database.valkey_client import (
            is_available as valkey_is_available,
        )

        if valkey_is_available():
            from src.core.ratelimit.storage import ValkeyStorage

            return ValkeyStorage()
    except Exception as e:
        logger.debug(f"Sticker rate-limit Valkey backend unavailable: {e}")
    try:
        db = api.get_db()
        if db is not None:
            from src.core.ratelimit.storage import DatabaseStorage

            return DatabaseStorage(db)
    except Exception as e:
        logger.debug(f"Sticker rate-limit database backend unavailable: {e}")
    return None


def _rate_limit_unavailable() -> NoReturn:
    """Fail closed with an honest 503 (never silently allow N x quota)."""
    raise HTTPException(
        status_code=503,
        detail={
            "error": {
                "code": 503,
                "message": "Sticker rate limiter unavailable. Please try again later.",
            }
        },
        headers={"Retry-After": "60"},
    )


async def _check_sticker_create_rate_limit(user_id: int) -> None:
    """Throttle sticker creation per user (fail-closed).

    Reuses the shared rate-limit manager configured at startup. If the
    manager is not set up yet, lazily initialise it with the shared
    persistent backend (Valkey, else the shared database) and the global
    limiter enabled. When no shared backend is available, fail closed
    with 503 + Retry-After instead of downgrading to per-process memory
    with the global limiter disabled (which grants N x quota under
    multi-worker).
    """
    import src.core.ratelimit as ratelimit

    if not ratelimit.is_setup():
        storage = _build_shared_ratelimit_storage()
        if storage is None:
            logger.warning(
                "Sticker rate limiter unavailable (no shared backend); "
                "failing closed with 503"
            )
            _rate_limit_unavailable()
        try:
            ratelimit.setup(storage_backend=storage, enable_global_limit=True)
        except Exception as e:
            logger.warning(f"Sticker rate-limit setup failed, failing closed: {e}")
            _rate_limit_unavailable()

    try:
        manager = ratelimit.get_manager()
    except Exception as e:
        logger.warning(f"Sticker rate-limit unavailable, failing closed: {e}")
        _rate_limit_unavailable()

    now_seconds = int(time.time())
    minute_window = now_seconds - (now_seconds % 60)
    key = f"stickers:create:minute:{user_id}:{minute_window}"
    # L3: a check-then-increment (read 0, then +1) lets concurrent requests
    # both read the same sub-limit count and both pass, so the effective
    # budget is N*limit under parallelism. increment_custom_usage is atomic
    # under the storage lock — do the increment in ONE call and treat the
    # returned counter as authoritative: requests 1..max pass, the (max+1)th
    # is rejected.
    try:
        count = await manager.aincrement_custom_usage(key, "count", 1, ttl=120)
    except Exception as e:
        logger.warning(f"Sticker rate-limit increment failed, failing closed: {e}")
        _rate_limit_unavailable()
    max_per_minute = 20
    if count > max_per_minute:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": "Sticker creation rate limit exceeded. Please try again later.",
                }
            },
        )


async def _dispatch_stickers_update(server_id: int, user_id: int):
    """Helper to dispatch GUILD_STICKERS_UPDATE via WebSocket."""
    try:
        from src.api.websocket import get_dispatcher, is_setup as ws_is_setup
        from src.core.events.models import Event
        from src.core.events.types import EventType

        if not ws_is_setup():
            return

        dispatcher = get_dispatcher()
        stickers_mod = api.get_stickers()
        servers = api.get_servers()

        if not stickers_mod or not servers:
            return

        # Get all server members
        user_ids = servers.get_member_user_ids(server_id)
        if not user_ids:
            return

        # Get current stickers for the payload. The acting user is already a
        # verified server member (the create/delete routes gate on membership),
        # so pass their id instead of the old hardcoded 0 that always failed the
        # membership check and silently suppressed the broadcast.
        packs = stickers_mod.get_server_packs(user_id, server_id)

        response_packs = []
        for pack in packs:
            pack_stickers = stickers_mod.get_pack_stickers(user_id, pack.id)
            pack_response = StickerPackResponse.model_validate(pack)
            pack_response.stickers = [
                StickerResponse.model_validate(s) for s in pack_stickers
            ]
            response_packs.append(pack_response.model_dump())

        # Create and dispatch the event
        event = Event(
            event_type=EventType.GUILD_STICKERS_UPDATE,
            data={
                "guild_id": str(server_id),
                "sticker_packs": response_packs,
            },
            server_id=server_id,
        )
        await dispatcher.dispatch_event(event, user_ids)

    except Exception as e:
        logger.debug(f"Failed to dispatch sticker update for server {server_id}: {e}")


@router.get(
    "/servers/{server_id}/stickers",
    response_model=List[StickerPackResponse],
    summary="Get server stickers",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        403: {"model": ErrorResponse, "description": "Not a member of this server"},
    },
)
async def get_server_stickers(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[StickerPackResponse]:
    """
    Get all sticker packs for a server.
    """
    stickers_mod = api.get_stickers()
    if not stickers_mod:
        raise HTTPException(status_code=500, detail="Stickers module not available")

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid server ID")

    try:
        packs = stickers_mod.get_server_packs(current_user.user_id, sid)

        # Batch the sticker lookups for all packs into one query instead of
        # one get_pack_stickers() call per pack (which re-checked membership
        # and re-queried the stickers table every time).
        stickers_by_pack = _batch_pack_stickers(
            stickers_mod, packs, current_user.user_id
        )

        # Hydrate packs with stickers
        response_packs = []
        for pack in packs:
            pack_response = StickerPackResponse.model_validate(pack)
            pack_response.stickers = [
                StickerResponse.model_validate(s)
                for s in stickers_by_pack.get(int(pack.id), [])
            ]
            response_packs.append(pack_response)

        return response_packs
    except HTTPException:
        raise
    except PermissionDeniedError:
        # The manager refuses a non-member with PermissionDeniedError; the
        # blanket 500 made a refusal indistinguishable from an outage (the
        # route's own responses block documents 403 for exactly this case).
        # Same mapping the sibling handlers in this module already use.
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Access denied"}},
        )
    except Exception as e:
        logger.error(f"Failed to fetch stickers for server {sid}: {e}")
        raise HTTPException(status_code=500, detail="Failed to load server stickers")


@router.post(
    "/servers/{server_id}/stickers",
    response_model=StickerResponse,
    summary="Create custom sticker",
)
async def create_sticker(
    server_id: str,
    name: str = Form(..., min_length=2, max_length=30),
    image: UploadFile = File(...),
    tags: Optional[str] = Form(None),  # Comma separated
    description: Optional[str] = Form(None, max_length=200),
    current_user: TokenInfo = Depends(get_current_user),
) -> StickerResponse:
    """
    Create a custom sticker for a server.
    """
    stickers_mod = api.get_stickers()
    if not stickers_mod:
        raise HTTPException(status_code=500, detail="Stickers module not available")

    # Throttle abusive sticker creation.
    await _check_sticker_create_rate_limit(current_user.user_id)

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid server ID")

    # Defence in depth: sticker name/tags/description are rendered to every
    # server member via the sticker picker, so reject HTML metacharacters
    # and control characters (stored XSS).
    # SecurityRejection (a ValueError) is mapped to a 400 by the app-level
    # handler, so the route no longer translates it by hand.
    name = _reject_ui_text_chars(name, "sticker name")
    tag_list = []
    if tags:
        if len(tags) > MAX_STICKER_TAG_PAYLOAD:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "A sticker may have at most 10 tags of 32 characters",
                    }
                },
            )
        tag_list = [
            _reject_ui_text_chars(t.strip(), "sticker tag")
            for t in tags.split(",")
            if t.strip()
        ]
        if len(tag_list) > 10 or any(len(t) > 32 for t in tag_list):
            raise HTTPException(
                status_code=400,
                detail={
                    "error": {
                        "code": 400,
                        "message": "A sticker may have at most 10 tags of 32 characters",
                    }
                },
            )
        tags = ",".join(tag_list)
    if description:
        description = _reject_ui_text_chars(description, "sticker description")

    try:
        # Cap the request body before reading it fully into memory.
        declared_size = image.size
        if declared_size is not None and declared_size > STICKER_MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Sticker file is too large",
            )

        # Get or create server pack
        packs = stickers_mod.get_server_packs(current_user.user_id, sid)
        target_pack = next((p for p in packs if p.pack_type == PackType.SERVER), None)

        if not target_pack:
            # Create default server pack
            target_pack = stickers_mod.create_pack(
                user_id=current_user.user_id,
                name="Server Stickers",
                server_id=sid,
                pack_type=PackType.SERVER,
            )

        # Read file
        content = await image.read()
        if len(content) > STICKER_MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail="Sticker file is too large",
            )

        sticker = stickers_mod.create_sticker_from_file(
            user_id=current_user.user_id,
            pack_id=target_pack.id,
            name=name,
            image_data=content,
            content_type=image.content_type,
            tags=tag_list,
            description=description,
        )

        # Dispatch update as background task (don't block response)
        import asyncio

        asyncio.create_task(_dispatch_stickers_update(sid, current_user.user_id))

        return StickerResponse.model_validate(sticker)

    except HTTPException:
        raise
    except (
        InvalidStickerFormatError,
        StickerTooLargeError,
        InvalidStickerNameError,
        StickerLimitError,
        PackLimitError,
        PackNotFoundError,
    ):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except PermissionDeniedError:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(f"Failed to create sticker: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create sticker")


@router.delete(
    "/stickers/{sticker_id}",
    summary="Delete sticker",
)
async def delete_sticker(
    sticker_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    """Delete a sticker."""
    stickers_mod = api.get_stickers()
    if not stickers_mod:
        raise HTTPException(status_code=500, detail="Stickers module not available")

    try:
        sid = int(sticker_id)
        # We need server_id for dispatching update after deletion
        # StickerManager.get_sticker should help
        sticker = stickers_mod.get_sticker(sid)
        if not sticker:
            raise HTTPException(status_code=404, detail="Sticker not found")

        pack = stickers_mod.get_pack(sticker.pack_id, current_user.user_id)
        server_id = pack.server_id if pack else None

        stickers_mod.remove_sticker(current_user.user_id, sid)

        # Dispatch update if server sticker
        if server_id:
            await _dispatch_stickers_update(server_id, current_user.user_id)

        return {"success": True}
    except StickerNotFoundError:
        # M2: a concurrent delete between the get and remove must surface
        # as 404, not the generic 500 from the catch-all below.
        raise HTTPException(status_code=404, detail="Sticker not found")
    except PermissionDeniedError:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except PackNotFoundError:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        logger.error(f"Failed to delete sticker {sticker_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete sticker")


@router.post(
    "/stickers/{sticker_id}/send",
    summary="Track sticker usage",
)
async def send_sticker_usage(
    request: Request,
    sticker_id: str,
    message_id: str = Form(...),
    current_user: TokenInfo = Depends(get_current_user),
):
    """Record sticker usage."""
    stickers_mod = api.get_stickers()
    if not stickers_mod:
        raise HTTPException(status_code=500, detail="Stickers module not available")

    # M3: usage tracking performs a DB write per call and was unlimited —
    # a tight loop amplified writes (plus the notification fan-out). Cap it
    # with the shared limiter, mirroring the reactions route's budget.
    rl_result = await ratelimit.acheck_rate_limit(
        user_id=current_user.user_id,
        route="POST /stickers/send",
        is_internal=getattr(request.state, "is_internal", False) if request else False,
    )
    if not rl_result.allowed:
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": f"Rate limited. Try again in {rl_result.retry_after}s",
                }
            },
            headers={"Retry-After": str(rl_result.retry_after)},
        )

    try:
        sid = int(sticker_id)
        mid = int(message_id)
        if not stickers_mod.can_use_sticker(current_user.user_id, sid):
            raise PermissionDeniedError(
                "You do not have permission to use this sticker"
            )
        stickers_mod.send_sticker(current_user.user_id, mid, sid)
        return {"success": True}
    except PermissionDeniedError:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    except Exception as e:
        # Log but don't fail hard if usage tracking fails
        logger.warning(f"Failed to track sticker usage: {e}")
        return {"success": False}


@router.get(
    "/stickers/suggestions",
    response_model=List[StickerResponse],
    summary="Get sticker suggestions",
)
async def get_sticker_suggestions(
    content: str = Query(..., max_length=MAX_STICKER_SUGGESTION_QUERY),
    server_id: Optional[str] = Query(None),
    limit: int = Query(default=5, ge=1, le=20),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[StickerResponse]:
    """Get sticker suggestions based on text content."""
    stickers_mod = api.get_stickers()
    if not stickers_mod:
        raise HTTPException(status_code=500, detail="Stickers module not available")

    # M1: parse inside the try so a non-numeric server_id answers 400
    # instead of an unhandled ValueError -> 500.
    try:
        sid = int(server_id) if server_id else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid server ID")

    try:
        suggestions = stickers_mod.get_sticker_suggestions(
            current_user.user_id, content, sid, limit
        )
        return [StickerResponse.model_validate(s.sticker) for s in suggestions]
    except Exception as e:
        logger.error(f"Sticker suggestion error: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail="Failed to load sticker suggestions"
        )
