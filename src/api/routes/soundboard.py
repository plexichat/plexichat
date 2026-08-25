"""
Soundboard routes - Server sound library endpoints.

All manage operations require the ``server.soundboard.manage`` scope;
playback is allowed for any member of the sound's server. Uploaded audio
bytes are validated and stored through the shared media pipeline.
"""

from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Depends, UploadFile

import src.api as api
import utils.logger as logger
import utils.config as config
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.soundboard import (
    SoundPermissionRequest,
    SoundPermissionResponse,
    SoundPlayRequest,
    SoundPlaybackResponse,
    SoundResponse,
    SoundUpdateRequest,
)
from src.api.schemas.common import ErrorResponse
from src.core.soundboard.exceptions import (
    ChannelNotFoundError,
    InvalidSoundFormatError,
    InvalidSoundNameError,
    InvalidSoundUrlError,
    NotInVoiceChannelError,
    PermissionDeniedError,
    ServerNotFoundError,
    SoundCooldownError,
    SoundLimitError,
    SoundNotFoundError,
    SoundTooLargeError,
    SoundTooLongError,
    SoundboardError,
)
from src.core.soundboard.models import SOUNDBOARD_MANAGE_PERMISSION

router = APIRouter(tags=["Soundboard"])


# Hard ceiling applied at the HTTP boundary so a single request cannot buffer
# an unbounded body into memory before the manager's own size check runs.
_HTTP_UPLOAD_CEILING_BYTES = 10 * 1024 * 1024


def _sound_to_response(sound) -> SoundResponse:
    """Convert a core Sound dataclass to the response model."""
    return SoundResponse(
        id=str(sound.id),
        server_id=str(sound.server_id),
        name=sound.name,
        format=sound.format.value,
        emoji=sound.emoji,
        url=sound.url,
        size=int(sound.size),
        duration_seconds=float(sound.duration_seconds),
        volume=float(sound.volume),
        cooldown_seconds=int(sound.cooldown_seconds or 0),
        created_by=str(sound.created_by),
        created_at=int(sound.created_at),
        usage_count=int(sound.usage_count),
    )


def _get_soundboard():
    """Resolve the soundboard module or fail with a 503."""
    soundboard = api.get_soundboard()
    if not soundboard:
        logger.error("Soundboard module not available")
        raise HTTPException(
            status_code=503,
            detail={
                "error": {"code": 503, "message": "Soundboard module not available"}
            },
        )
    try:
        # Touch the manager so an un-initialised module surfaces as 503
        # instead of a RuntimeError deep inside a handler.
        soundboard._get_manager()
    except RuntimeError:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {"code": 503, "message": "Soundboard module not initialized"}
            },
        )
    return soundboard


def _parse_server_id(server_id: str) -> int:
    try:
        return int(server_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid server ID"}},
        )


def _parse_int(value: str, label: str) -> int:
    try:
        return int(value)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Invalid {label}"}},
        )


def _require_manage_permission(user_id: int, server_id: int) -> None:
    """HTTP-level gate for manage operations (manager re-checks, fails closed)."""
    servers = api.get_servers()
    if not servers:
        # W5 (route gate): silently returning here let manage requests
        # proceed to the manager un-gated at the HTTP layer when the servers
        # module is absent. Fail with 503 like _get_soundboard instead.
        logger.error("Servers module not available for soundboard permission check")
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Servers module not available"}},
        )
    try:
        perms = servers.get_permissions(user_id, server_id)
    except Exception as e:
        logger.error(f"Failed to load permissions for server {server_id}: {e}")
        raise HTTPException(
            status_code=403,
            detail={
                "error": {"code": 403, "message": "Missing soundboard permissions"}
            },
        )
    from src.core.auth.permissions import has_permission

    if not (
        has_permission(perms, SOUNDBOARD_MANAGE_PERMISSION)
        or has_permission(perms, "server.manage")
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Missing server.soundboard.manage permission",
                }
            },
        )


def _map_soundboard_error(e: Exception) -> HTTPException:
    """Translate core soundboard errors into safe HTTP responses."""
    if isinstance(e, (SoundNotFoundError, ServerNotFoundError)):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, ChannelNotFoundError):
        return HTTPException(status_code=404, detail=str(e))
    if isinstance(e, NotInVoiceChannelError):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, PermissionDeniedError):
        return HTTPException(status_code=403, detail=str(e))
    if isinstance(e, SoundCooldownError):
        return HTTPException(status_code=429, detail=str(e))
    if isinstance(
        e,
        (
            InvalidSoundFormatError,
            InvalidSoundNameError,
            InvalidSoundUrlError,
            SoundTooLargeError,
            SoundTooLongError,
            SoundLimitError,
        ),
    ):
        return HTTPException(status_code=400, detail=str(e))
    if isinstance(e, SoundboardError):
        return HTTPException(status_code=400, detail=str(e))
    logger.error(f"Unhandled soundboard error: {e}", exc_info=True)
    return HTTPException(status_code=500, detail="Soundboard operation failed")


@router.get(
    "/{server_id}/soundboard",
    response_model=List[SoundResponse],
    summary="List server sounds",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid server ID"},
        403: {"model": ErrorResponse, "description": "Not a member of this server"},
    },
)
async def list_server_sounds(
    server_id: str, current_user: TokenInfo = Depends(get_current_user)
) -> List[SoundResponse]:
    """List all sounds in a server's soundboard (members only)."""
    soundboard = _get_soundboard()
    sid = _parse_server_id(server_id)
    try:
        sounds = soundboard.get_server_sounds(current_user.user_id, sid)
        return [_sound_to_response(s) for s in sounds]
    except SoundboardError as e:
        raise _map_soundboard_error(e)
    except Exception as e:
        logger.error(f"Failed to list sounds for server {sid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list sounds")


@router.post(
    "/{server_id}/soundboard",
    response_model=SoundResponse,
    summary="Upload a sound",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid upload"},
        403: {"model": ErrorResponse, "description": "Missing permission"},
        404: {"model": ErrorResponse, "description": "Server not found"},
        413: {"model": ErrorResponse, "description": "File too large"},
    },
)
async def upload_sound(
    server_id: str,
    name: str = Form(..., min_length=1, max_length=64),
    file: UploadFile = File(...),
    emoji: Optional[str] = Form(None),
    volume: float = Form(1.0),
    duration_seconds: Optional[float] = Form(None),
    current_user: TokenInfo = Depends(get_current_user),
) -> SoundResponse:
    """
    Upload audio bytes as a server sound.

    Bytes are validated with magic-byte container detection against the
    configured allow-list and stored through the shared media pipeline;
    client-declared url/size/duration are never trusted.
    """
    soundboard = _get_soundboard()
    sid = _parse_server_id(server_id)

    _require_manage_permission(current_user.user_id, sid)

    max_size = int(config.get("soundboard.max_sound_size", 2097152) or 2097152)
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > max_size:
        raise HTTPException(status_code=413, detail="Sound file is too large")

    try:
        content = await file.read()
    except Exception as e:
        logger.error(f"Failed reading sound upload: {e}")
        raise HTTPException(status_code=400, detail="Could not read uploaded file")
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="Sound file is too large")
    if len(content) > _HTTP_UPLOAD_CEILING_BYTES:
        raise HTTPException(status_code=413, detail="Sound file is too large")

    filename = getattr(file, "filename", None) or f"{name}.{file.content_type}"

    try:
        sound = soundboard.create_sound_from_upload(
            user_id=current_user.user_id,
            server_id=sid,
            name=name,
            file_data=content,
            filename=filename,
            content_type=file.content_type,
            duration_seconds=duration_seconds,
            emoji=emoji,
            volume=max(0.0, min(1.0, float(volume))),
        )
        return _sound_to_response(sound)
    except SoundboardError as e:
        raise _map_soundboard_error(e)
    except RuntimeError as e:
        logger.error(f"Sound upload infrastructure failure: {e}")
        raise HTTPException(status_code=503, detail="Media storage unavailable")
    except Exception as e:
        logger.error(f"Failed to upload sound to server {sid}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to upload sound")


@router.get(
    "/{server_id}/soundboard/{sound_id}",
    response_model=SoundResponse,
    summary="Get a sound",
)
async def get_sound(
    server_id: str,
    sound_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SoundResponse:
    """Fetch a single sound from the server's soundboard."""
    soundboard = _get_soundboard()
    _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")
    sound = soundboard.get_sound(sid_sound, current_user.user_id)
    if not sound:
        raise HTTPException(status_code=404, detail="Sound not found")
    return _sound_to_response(sound)


@router.patch(
    "/{server_id}/soundboard/{sound_id}",
    response_model=SoundResponse,
    summary="Update sound metadata",
)
async def update_sound(
    server_id: str,
    sound_id: str,
    body: SoundUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SoundResponse:
    """Update editable metadata (name, emoji, volume, cooldown)."""
    soundboard = _get_soundboard()
    _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")
    try:
        sound = soundboard.update_sound(
            current_user.user_id,
            sid_sound,
            name=body.name,
            emoji=body.emoji,
            volume=body.volume,
            cooldown=body.cooldown,
        )
        return _sound_to_response(sound)
    except SoundboardError as e:
        raise _map_soundboard_error(e)
    except Exception as e:
        logger.error(f"Failed to update sound {sid_sound}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update sound")


@router.delete(
    "/{server_id}/soundboard/{sound_id}",
    summary="Delete a sound",
    responses={
        403: {"model": ErrorResponse, "description": "Missing permission"},
        404: {"model": ErrorResponse, "description": "Sound not found"},
    },
)
async def delete_sound(
    server_id: str,
    sound_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Remove a sound from the server's soundboard."""
    soundboard = _get_soundboard()
    sid = _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")

    _require_manage_permission(current_user.user_id, sid)

    try:
        deleted = soundboard.delete_sound(current_user.user_id, sid_sound)
        return {"success": bool(deleted)}
    except SoundboardError as e:
        raise _map_soundboard_error(e)
    except Exception as e:
        logger.error(f"Failed to delete sound {sid_sound}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete sound")


@router.put(
    "/{server_id}/soundboard/{sound_id}/permissions/{role_id}",
    response_model=SoundPermissionResponse,
    summary="Set role playback permission",
)
async def set_sound_permission(
    server_id: str,
    sound_id: str,
    role_id: str,
    body: SoundPermissionRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SoundPermissionResponse:
    """Allow or deny a role from playing this sound (manage scope required)."""
    soundboard = _get_soundboard()
    sid = _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")
    rid = _parse_int(role_id, "role ID")

    _require_manage_permission(current_user.user_id, sid)

    try:
        perm = soundboard.set_sound_permissions(
            current_user.user_id, sid_sound, rid, body.can_use
        )
        return SoundPermissionResponse(
            id=str(perm.id),
            sound_id=str(perm.sound_id),
            role_id=str(perm.role_id),
            can_use=bool(perm.can_use),
        )
    except SoundboardError as e:
        raise _map_soundboard_error(e)
    except Exception as e:
        logger.error(f"Failed to set sound permissions {sid_sound}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to set permissions")


@router.post(
    "/{server_id}/soundboard/{sound_id}/play",
    response_model=SoundPlaybackResponse,
    summary="Play a sound in a voice channel",
    responses={
        403: {"model": ErrorResponse, "description": "No permission to use sound"},
        404: {"model": ErrorResponse, "description": "Sound or channel not found"},
        429: {"model": ErrorResponse, "description": "Sound is on cooldown"},
    },
)
async def play_sound(
    server_id: str,
    sound_id: str,
    body: SoundPlayRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SoundPlaybackResponse:
    """Trigger playback tracking for a sound in a voice channel."""
    soundboard = _get_soundboard()
    _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")
    cid = _parse_int(body.channel_id, "channel ID")
    try:
        playback = soundboard.play_sound(
            current_user.user_id, sid_sound, cid, volume=body.volume
        )
        return SoundPlaybackResponse(
            sound=_sound_to_response(playback.sound),
            user_id=str(playback.user_id),
            channel_id=str(playback.channel_id),
            timestamp=int(playback.timestamp),
        )
    except SoundboardError as e:
        raise _map_soundboard_error(e)
    except Exception as e:
        logger.error(f"Failed to play sound {sid_sound}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to play sound")
