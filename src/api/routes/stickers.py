"""
Sticker routes - Sticker and pack management endpoints.
"""

from typing import List, Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, Form, Query

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.core.database.cache import cached, invalidate_pattern
from src.api.schemas.stickers import StickerResponse, StickerPackResponse
from src.api.schemas.common import ErrorResponse
from src.core.stickers import PackType, StickerFormat

router = APIRouter(tags=["Stickers"])


async def _dispatch_stickers_update(server_id: int):
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

        # Get current stickers for the payload
        packs = stickers_mod.get_server_packs(0, server_id)

        response_packs = []
        for pack in packs:
            pack_stickers = stickers_mod.get_pack_stickers(0, pack.id)
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

        # Hydrate packs with stickers
        response_packs = []
        for pack in packs:
            pack_stickers = stickers_mod.get_pack_stickers(
                current_user.user_id, pack.id
            )
            pack_response = StickerPackResponse.model_validate(pack)
            pack_response.stickers = [
                StickerResponse.model_validate(s) for s in pack_stickers
            ]
            response_packs.append(pack_response)

        return response_packs
    except Exception as e:
        logger.error(f"Failed to fetch stickers for server {sid}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    current_user: TokenInfo = Depends(get_current_user),
) -> StickerResponse:
    """
    Create a custom sticker for a server.
    """
    stickers_mod = api.get_stickers()
    if not stickers_mod:
        raise HTTPException(status_code=500, detail="Stickers module not available")

    try:
        sid = int(server_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid server ID")

    try:
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

        # Parse tags
        tag_list = [t.strip() for t in tags.split(",")] if tags else []

        # Read file
        content = await image.read()

        sticker = stickers_mod.create_sticker_from_file(
            user_id=current_user.user_id,
            pack_id=target_pack.id,
            name=name,
            image_data=content,
            content_type=image.content_type,
            tags=tag_list,
        )

        # Dispatch update
        await _dispatch_stickers_update(sid)

        return StickerResponse.model_validate(sticker)

    except Exception as e:
        logger.error(f"Failed to create sticker: {e}", exc_info=True)
        raise HTTPException(status_code=400, detail=str(e))


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
            await _dispatch_stickers_update(server_id)

        return {"success": True}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post(
    "/stickers/{sticker_id}/send",
    summary="Track sticker usage",
)
async def send_sticker_usage(
    sticker_id: str,
    message_id: str = Form(...),
    current_user: TokenInfo = Depends(get_current_user),
):
    """Record sticker usage."""
    stickers_mod = api.get_stickers()
    if not stickers_mod:
        raise HTTPException(status_code=500, detail="Stickers module not available")

    try:
        sid = int(sticker_id)
        mid = int(message_id)
        stickers_mod.send_sticker(current_user.user_id, mid, sid)
        return {"success": True}
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
    content: str = Query(...),
    server_id: Optional[str] = Query(None),
    limit: int = Query(default=5, ge=1, le=20),
    current_user: TokenInfo = Depends(get_current_user),
) -> List[StickerResponse]:
    """Get sticker suggestions based on text content."""
    stickers_mod = api.get_stickers()
    if not stickers_mod:
        raise HTTPException(status_code=500, detail="Stickers module not available")

    sid = int(server_id) if server_id else None

    try:
        suggestions = stickers_mod.get_sticker_suggestions(
            current_user.user_id, content, sid, limit
        )
        return [StickerResponse.model_validate(s.sticker) for s in suggestions]
    except Exception as e:
        logger.error(f"Sticker suggestion error: {e}")
        return []
