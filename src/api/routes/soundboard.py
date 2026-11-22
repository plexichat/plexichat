"""
Soundboard routes - Server sound library endpoints.

All manage operations require the ``server.soundboard.manage`` scope;
playback is allowed for any member of the sound's server. Uploaded audio
bytes are validated and stored through the shared media pipeline.
"""

from src.api.request_guards import MAX_SOUND_UPLOAD, read_upload_capped

from src.core.avatars.manager.upload_guard import sanitize_upload_filename
from src.core.security.hardening import (
    HardeningViolation,
    bounded_int,
    clamp_finite_float,
    parse_id,
)

from typing import Annotated, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Depends, Query, UploadFile

from src.core.database import cached, invalidate_pattern

import src.api as api
import utils.logger as logger
import utils.config as config
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.soundboard import (
    SoundPermissionRequest,
    SoundPermissionResponse,
    SoundPlayRequest,
    SoundPlaybackResponse,
    SoundResponse,
    SoundUpdateRequest,
)
from src.api.schemas.common import ErrorResponse, _reject_ui_text_chars
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

# A server's sound library is effectively static between manage operations.
# Memoize the hydrated page for a short window; membership is checked live on
# the cold path and the cache is dropped on every sound mutation and on member
# kick/ban/leave (servers/members.py::_invalidate_member_access_caches).
_SOUNDS_LIST_CACHE_TTL = 10
_SOUNDS_LIST_CACHE_PREFIX = "server_sounds_api"


def _bounded_sound_size(value: object) -> int:
    """Clamp the configured sound size cap to the HTTP upload ceiling."""
    return bounded_int(
        value,
        "soundboard.max_sound_size",
        minimum=1,
        maximum=_HTTP_UPLOAD_CEILING_BYTES,
    )


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
    """Parse a server id through the shared ASCII-only snowflake gate.

    Bare ``int(str(server_id).strip())`` accepted Unicode decimal homoglyphs
    (``"１２"`` -> 12), PEP 515 underscores (``"1_2"`` -> 12) and arbitrarily
    large values. Those spellings reached permission checks, cache keys and
    storage as a *different* identifier than the caller wrote, and disagreed
    with every other route boundary that parses ids with ``parse_id``. Route
    them all through the canonical gate so validation and consumption share a
    single interpretation of the token; non-canonical ids fail with 400 before
    any service call.
    """
    try:
        return parse_id(server_id, "server ID")
    except HardeningViolation as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid server ID"}},
        ) from exc


def _parse_int(value: str, label: str) -> int:
    """Parse a positive snowflake-style id with the same canonical gate.

    Sound, role and channel ids carry the same range/grammar constraints as a
    server id. ``int()`` also truncated nothing here but accepted homoglyphs,
    underscores and oversized/negative values that the rest of the route
    layer rejects outright.
    """
    try:
        return parse_id(value, label)
    except HardeningViolation as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": f"Invalid {label}"}},
        ) from exc


def _require_sound_in_server(sound, server_id: int):
    """Ensure a loaded sound belongs to the URL server, else 404.

    The ``/{server_id}/soundboard/{sound_id}`` routes previously validated
    ``server_id`` but discarded it, so ``GET /999/soundboard/<id-of-456>``
    returned 200 whenever the caller could see the sound's real server.
    Mismatched (or missing, for non-members) sounds 404 without leaking
    which server actually owns the row.
    """
    if sound is None or int(getattr(sound, "server_id", -1)) != int(server_id):
        raise HTTPException(status_code=404, detail="Sound not found")
    return sound


def _cheap_require_sound_in_server(
    soundboard, sound_id: int, server_id: int, user_id: int
) -> None:
    """Cross-server guard for PATCH without reloading the whole sound.

    BEFORE: ``update_sound`` called ``get_sound`` for the guard and then
    ``update_sound`` immediately called ``get_sound`` again (JOIN over usage
    rows, membership probe, full Sound reconstruction). AFTER: read only the
    owning ``server_id`` column for the guard. The manager re-fetches the
    sound and re-validates membership/permissions itself, so validation is
    unchanged. The full-object path is kept as a fallback when the direct
    lookup is unavailable.
    """
    row = None
    try:
        db = getattr(soundboard._get_manager(), "_db", None)
        if db is not None and hasattr(db, "fetch_one"):
            row = db.fetch_one(
                "SELECT server_id FROM soundboard_sounds WHERE id = ?", (sound_id,)
            )
    except Exception as e:
        logger.debug(f"cheap sound server lookup failed: {e}")
        row = None

    if row is not None:
        if int(row["server_id"]) != int(server_id):
            raise HTTPException(status_code=404, detail="Sound not found")
        return

    _require_sound_in_server(soundboard.get_sound(sound_id, user_id), server_id)


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
        return HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": "Not found"}}
        )
    if isinstance(e, ChannelNotFoundError):
        return HTTPException(
            status_code=404, detail={"error": {"code": 404, "message": "Not found"}}
        )
    if isinstance(e, NotInVoiceChannelError):
        return HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    if isinstance(e, PermissionDeniedError):
        return HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": "Access denied"}}
        )
    if isinstance(e, SoundCooldownError):
        return HTTPException(
            status_code=429,
            detail={"error": {"code": 429, "message": "Rate limit exceeded"}},
        )
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
        return HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    if isinstance(e, SoundboardError):
        return HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid request"}},
        )
    logger.error(f"Unhandled soundboard error: {e}", exc_info=True)
    return HTTPException(status_code=500, detail="Soundboard operation failed")


def _require_server_member_for_library(sid: int, user_id: int) -> None:
    """Re-check membership on EVERY request, before the cached page is read.

    ``_load_server_sounds_page`` checks membership inside its cold path via
    ``get_server_sounds``, but a cache hit skips that body entirely. Core
    membership revocations (kick/ban/leave performed by automod, an admin path
    or a bot) clear the core membership caches but do not know about this HTTP
    response namespace, so a removed member could otherwise keep reading a
    still-warm 200 until the short TTL elapsed. Run an independent, fail-closed
    gate ahead of the cache so revocation is visible immediately no matter which
    path performed it. Mirrors the emoji routes' membership gate.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        logger.error("Servers module not available for sound library read")
        raise HTTPException(
            status_code=503,
            detail={"error": {"code": 503, "message": "Servers module not available"}},
        )
    try:
        member = servers_mod.get_member(sid, user_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            "Membership check failed for sound library read of server %s: %s",
            sid,
            exc,
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={
                "error": {
                    "code": 500,
                    "message": "Failed to verify server membership",
                }
            },
        ) from exc
    if not member:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Not a member of this server"}},
        )


@cached(ttl=_SOUNDS_LIST_CACHE_TTL, prefix=_SOUNDS_LIST_CACHE_PREFIX)
async def _load_server_sounds_page(
    sid: int, user_id: int, limit: int, offset: int
) -> List[SoundResponse]:
    """Cold path for a server's sound library (membership checked live)."""
    soundboard = _get_soundboard()
    sounds = soundboard.get_server_sounds(user_id, sid)
    # IDOR defence in depth: never surface a sound owned by another server,
    # even if a buggy manager returns one. Membership was already checked.
    sounds = [s for s in sounds if int(getattr(s, "server_id", sid)) == int(sid)]
    # Bounded page so one request cannot serialize an unbounded library.
    return [_sound_to_response(s) for s in sounds[offset : offset + limit]]


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
    server_id: str,
    limit: Annotated[int, Query(ge=1, le=200)] = 200,
    offset: Annotated[int, Query(ge=0, le=1000000)] = 0,
    current_user: TokenInfo = Depends(get_current_user),
) -> List[SoundResponse]:
    """List a bounded page of a server's sounds (members only)."""
    sid = _parse_server_id(server_id)
    # Live authz gate BEFORE the cache lookup: a revoked member must not be
    # served a still-warm library page, even if the revocation path did not
    # invalidate this cache namespace.
    _require_server_member_for_library(sid, current_user.user_id)
    try:
        page = await _load_server_sounds_page(sid, current_user.user_id, limit, offset)
        # A cache hit may hand back plain dicts; re-validate so the response is
        # identical whether the page came from the DB or the memo.
        return [SoundResponse.model_validate(s) for s in page]
    except HTTPException:
        raise
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
    # A bot token's user_id is its human owner. Without this gate a narrow
    # bot credential wields the owner's server roles to manage sounds and the
    # media upload is charged to the owner's storage quota. Match the emoji
    # and sticker management routes.
    require_user_token(current_user)
    soundboard = _get_soundboard()
    sid = _parse_server_id(server_id)

    _require_manage_permission(current_user.user_id, sid)

    # Defence in depth: sound name/emoji are rendered to every server member
    # via the soundboard picker, so reject HTML metacharacters and control
    # characters (stored XSS).
    # SecurityRejection (a ValueError) is mapped to a 400 by the app-level
    # handler, so the route no longer translates it by hand.
    name = _reject_ui_text_chars(name, "sound name")
    if emoji:
        emoji = _reject_ui_text_chars(emoji, "sound emoji")

    try:
        max_size = _bounded_sound_size(
            config.get("soundboard.max_sound_size", 2097152) or 2097152
        )
    except HardeningViolation as exc:
        raise HTTPException(
            status_code=400, detail="Invalid sound size configuration"
        ) from exc
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > max_size:
        raise HTTPException(status_code=413, detail="Sound file is too large")

    try:
        content = await read_upload_capped(file, MAX_SOUND_UPLOAD)
    except Exception as e:
        logger.error(f"Failed reading sound upload: {e}")
        raise HTTPException(status_code=400, detail="Could not read uploaded file")
    if len(content) > max_size:
        raise HTTPException(status_code=413, detail="Sound file is too large")
    if len(content) > _HTTP_UPLOAD_CEILING_BYTES:
        raise HTTPException(status_code=413, detail="Sound file is too large")

    # DEEP: never forward a client-supplied path to the storage layer.
    filename = sanitize_upload_filename(
        getattr(file, "filename", None), default=f"{name}.bin"
    )

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
            # ``volume`` is an unconstrained Form float; "nan"/"inf" parse to
            # non-finite floats. The old max/min clamp turned NaN into 1.0
            # (loudest) instead of failing safe.
            volume=clamp_finite_float(volume, 0.0, 1.0, default=0.0),
        )
        invalidate_pattern(f"{_SOUNDS_LIST_CACHE_PREFIX}:*")
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
    sid = _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")
    sound = soundboard.get_sound(sid_sound, current_user.user_id)
    _require_sound_in_server(sound, sid)
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
    require_user_token(current_user)
    soundboard = _get_soundboard()
    sid = _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")
    _cheap_require_sound_in_server(soundboard, sid_sound, sid, current_user.user_id)
    try:
        sound = soundboard.update_sound(
            current_user.user_id,
            sid_sound,
            name=body.name,
            emoji=body.emoji,
            volume=body.volume,
            cooldown=body.cooldown,
        )
        invalidate_pattern(f"{_SOUNDS_LIST_CACHE_PREFIX}:*")
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
    require_user_token(current_user)
    soundboard = _get_soundboard()
    sid = _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")

    _require_manage_permission(current_user.user_id, sid)
    _require_sound_in_server(soundboard.get_sound(sid_sound, current_user.user_id), sid)

    try:
        deleted = soundboard.delete_sound(current_user.user_id, sid_sound)
        if deleted:
            invalidate_pattern(f"{_SOUNDS_LIST_CACHE_PREFIX}:*")
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
    require_user_token(current_user)
    soundboard = _get_soundboard()
    sid = _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")
    rid = _parse_int(role_id, "role ID")

    _require_manage_permission(current_user.user_id, sid)
    scoped = soundboard.get_sound(sid_sound, current_user.user_id)
    _require_sound_in_server(scoped, sid)

    try:
        perm = soundboard.set_sound_permissions(
            current_user.user_id, sid_sound, rid, body.can_use
        )
        invalidate_pattern(f"{_SOUNDS_LIST_CACHE_PREFIX}:*")
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
    sid = _parse_server_id(server_id)
    sid_sound = _parse_int(sound_id, "sound ID")
    cid = _parse_int(body.channel_id, "channel ID")
    _require_sound_in_server(soundboard.get_sound(sid_sound, current_user.user_id), sid)
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
