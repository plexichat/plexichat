"""
Feature expansion API routes - New feature endpoints.

Provides REST API endpoints for the new features:
- Bookmarks: Per-user message bookmarks
- Scheduled messages: Schedule messages for future delivery
- Message forwarding: Forward messages between conversations
- Voice messages: Send voice message attachments
- User profiles: Custom status, bio, social links
- Push tokens: Register/manage mobile push notification tokens
- Last chat: Save/restore last active conversation
- Thread slowmode: Set/check thread slowmode
"""

from fastapi import APIRouter, HTTPException, Depends, Query, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import Optional, List

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse

router = APIRouter(tags=["Features"])


# ==================== Schemas ====================


class BookmarkRequest(BaseModel):
    message_id: str = Field(..., description="ID of the message to bookmark")
    conversation_id: str = Field(..., description="ID of the conversation")
    label: Optional[str] = Field(None, max_length=100, description="Optional label")


class ScheduledMessageRequest(BaseModel):
    conversation_id: str = Field(..., description="Target conversation ID")
    content: str = Field(
        ..., min_length=1, max_length=4000, description="Message content"
    )
    scheduled_at: int = Field(..., description="Timestamp (ms) when to send")


class ForwardMessageRequest(BaseModel):
    message_id: str = Field(..., description="ID of the message to forward")
    target_conversation_id: str = Field(..., description="Target conversation ID")


class VoiceMessageRequest(BaseModel):
    conversation_id: str = Field(..., description="Target conversation ID")
    duration_ms: int = Field(..., gt=0, description="Duration in milliseconds")
    filename: str = Field(..., min_length=1, description="Audio filename")
    content_type: str = Field(..., description="MIME type (audio/*)")
    size: int = Field(..., gt=0, description="File size in bytes")
    url: str = Field(..., description="Storage URL")
    waveform: Optional[str] = Field(None, description="Base64 waveform data")


class ProfileUpdateRequest(BaseModel):
    bio: Optional[str] = Field(None, max_length=1000, description="Profile bio")
    status: Optional[str] = Field(
        None, max_length=128, description="Custom status text"
    )
    status_emoji: Optional[str] = Field(None, max_length=32, description="Status emoji")
    pronouns: Optional[str] = Field(None, max_length=40, description="Pronouns")
    location: Optional[str] = Field(None, max_length=100, description="Location")
    timezone: Optional[str] = Field(None, max_length=64, description="Timezone")
    banner_url: Optional[str] = Field(None, description="Banner image URL")
    social_links: Optional[List[dict]] = Field(None, description="Social link objects")


class PushTokenRequest(BaseModel):
    token: str = Field(..., min_length=1, description="Push notification token")
    platform: str = Field(..., description="Platform: ios, android, or web")
    device_id: Optional[str] = Field(None, description="Device identifier")
    app_version: Optional[str] = Field(None, description="App version string")


class LastChatRequest(BaseModel):
    conversation_id: str = Field(..., description="Last active conversation ID")
    last_message_id: Optional[str] = Field(None, description="Last visible message ID")
    scroll_position: Optional[int] = Field(
        None, description="Scroll position for restoration"
    )


class ThreadSlowmodeRequest(BaseModel):
    interval_ms: int = Field(
        ..., ge=0, description="Slowmode interval in ms (0 to disable)"
    )


# ==================== Bookmarks ====================


@router.post(
    "/bookmarks",
    summary="Bookmark a message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def add_bookmark(
    body: BookmarkRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """Bookmark a message for your own reference."""
    messaging = api.get_messaging()
    if not messaging:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    try:
        message_id = int(body.message_id)
        conversation_id = int(body.conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid ID format"}},
        )

    try:
        bookmark_svc = (
            messaging._bookmark_svc if hasattr(messaging, "_bookmark_svc") else None
        )
        if not bookmark_svc:
            from src.core.messaging.services.bookmarks import BookmarkService

            db = api.get_db()
            bookmark_svc = BookmarkService(db, messaging)

        result = bookmark_svc.add_bookmark(
            user_id=current_user.user_id,
            message_id=message_id,
            conversation_id=conversation_id,
            label=body.label,
        )
        return {"success": True, "bookmark": result}
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )


@router.delete(
    "/bookmarks/{message_id}",
    summary="Remove a bookmark",
    responses={401: {"model": ErrorResponse}},
)
async def remove_bookmark(
    message_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    """Remove a bookmark from a message."""
    try:
        mid = int(message_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid ID format"}},
        )

    messaging = api.get_messaging()
    db = api.get_db()
    from src.core.messaging.services.bookmarks import BookmarkService

    bookmark_svc = BookmarkService(db, messaging)
    bookmark_svc.remove_bookmark(current_user.user_id, mid)
    return {"success": True}


@router.get(
    "/bookmarks",
    summary="List your bookmarks",
    responses={401: {"model": ErrorResponse}},
)
async def list_bookmarks(
    conversation_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
):
    """List your message bookmarks."""
    messaging = api.get_messaging()
    db = api.get_db()
    from src.core.messaging.services.bookmarks import BookmarkService

    bookmark_svc = BookmarkService(db, messaging)

    conv_id = int(conversation_id) if conversation_id else None
    results = bookmark_svc.get_bookmarks(current_user.user_id, conv_id, limit)
    return {"bookmarks": results}


# ==================== Scheduled Messages ====================


@router.post(
    "/scheduled-messages",
    summary="Schedule a message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def create_scheduled_message(
    body: ScheduledMessageRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """Schedule a message for future delivery."""
    try:
        conversation_id = int(body.conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid conversation ID"}},
        )

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.scheduled import ScheduledMessageService

    svc = ScheduledMessageService(db, participant_svc)

    try:
        result = svc.create_scheduled_message(
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            content=body.content,
            scheduled_at=body.scheduled_at,
        )
        return {"success": True, "scheduled_message": result}
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )


@router.get(
    "/scheduled-messages",
    summary="List your scheduled messages",
    responses={401: {"model": ErrorResponse}},
)
async def list_scheduled_messages(
    conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
):
    """List your scheduled messages."""
    db = api.get_db()
    from src.core.messaging.services.scheduled import ScheduledMessageService

    svc = ScheduledMessageService(db)

    conv_id = int(conversation_id) if conversation_id else None
    results = svc.list_scheduled_messages(current_user.user_id, conv_id, status, limit)
    return {"scheduled_messages": results}


@router.delete(
    "/scheduled-messages/{scheduled_id}",
    summary="Cancel a scheduled message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def cancel_scheduled_message(
    scheduled_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    """Cancel a pending scheduled message."""
    try:
        sid = int(scheduled_id)
    except ValueError:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": "Invalid ID"}}
        )

    db = api.get_db()
    from src.core.messaging.services.scheduled import ScheduledMessageService

    svc = ScheduledMessageService(db)

    try:
        svc.cancel_scheduled_message(sid, current_user.user_id)
        return {"success": True}
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )


# ==================== Message Forwarding ====================


@router.post(
    "/forward",
    summary="Forward a message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def forward_message(
    body: ForwardMessageRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """Forward a message to another conversation with attribution."""
    try:
        message_id = int(body.message_id)
        target_conversation_id = int(body.target_conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid ID format"}},
        )

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.forwarding import ForwardingService

    svc = ForwardingService(db, messaging, participant_svc)

    try:
        result = svc.forward_message(
            current_user.user_id, message_id, target_conversation_id
        )
        return {"success": True, "forward": result}
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )


# ==================== Voice Messages ====================


@router.post(
    "/voice-messages",
    summary="Send a voice message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def send_voice_message(
    body: VoiceMessageRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """Send a voice message as an audio attachment."""
    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.voice import VoiceMessageService

    svc = VoiceMessageService(db)

    try:
        result = svc.create_voice_message(
            user_id=current_user.user_id,
            conversation_id=int(body.conversation_id),
            duration_ms=body.duration_ms,
            filename=body.filename,
            content_type=body.content_type,
            size=body.size,
            url=body.url,
            waveform=body.waveform,
            participant_svc=participant_svc,
        )

        # Send the actual message via messaging module if available
        if messaging and result.get("attachment_data"):
            try:
                msg = messaging.send_message(
                    user_id=current_user.user_id,
                    conversation_id=int(body.conversation_id),
                    content="🎤 Voice message",
                    attachments=[result["attachment_data"]],
                )
                result["message_id"] = msg.id
            except Exception as e:
                logger.error(f"Failed to send voice message via messaging: {e}")

        return {"success": True, "voice_message": result}
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )


# ==================== Voice Message Upload (multipart) ====================


@router.post(
    "/voice-messages/upload",
    summary="Upload a voice message audio file",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def upload_voice_message(
    conversation_id: str = Form(..., description="Target conversation ID"),
    duration_ms: int = Form(..., gt=0, description="Duration in milliseconds"),
    audio: UploadFile = File(..., description="Audio file (webm, ogg, mp3, wav, opus)"),
    waveform: Optional[str] = Form(None, description="Base64 waveform data"),
    current_user: TokenInfo = Depends(get_current_user),
):
    """
    Upload a voice message as a multipart audio file.

    Accepts audio/webm, audio/ogg, audio/mp3, audio/wav, audio/opus.
    Maximum file size: 10MB. Maximum duration: 10 minutes.
    """
    # Validate content type
    allowed_content_types = {
        "audio/webm",
        "audio/ogg",
        "audio/mp3",
        "audio/mpeg",
        "audio/wav",
        "audio/x-wav",
        "audio/opus",
    }
    if not audio.content_type or audio.content_type not in allowed_content_types:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Invalid content type '{audio.content_type}'. "
                    f"Allowed: {', '.join(sorted(allowed_content_types))}",
                }
            },
        )

    # Validate file size (10MB max)
    MAX_UPLOAD_SIZE = 10 * 1024 * 1024
    content = await audio.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": "Audio file exceeds 10MB maximum size",
                }
            },
        )
    if len(content) == 0:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Audio file is empty"}},
        )

    # Validate duration
    MAX_DURATION_MS = 600000  # 10 minutes
    if duration_ms > MAX_DURATION_MS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": "Voice message exceeds 10 minute maximum duration",
                }
            },
        )

    try:
        conv_id = int(conversation_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid conversation ID"}},
        )

    # Store the audio file via media module
    try:
        media = api.get_media()
        ext_map = {
            "audio/webm": "webm",
            "audio/ogg": "ogg",
            "audio/mp3": "mp3",
            "audio/mpeg": "mp3",
            "audio/wav": "wav",
            "audio/x-wav": "wav",
            "audio/opus": "opus",
        }
        ext = ext_map.get(audio.content_type, "webm")
        filename = f"voice_{current_user.user_id}_{int(__import__('time').time() * 1000)}.{ext}"

        if media:
            from io import BytesIO

            file_obj = BytesIO(content)

            result = media.upload_file(
                file_obj=file_obj,
                filename=filename,
                content_type=audio.content_type,
                user_id=current_user.user_id,
                conversation_id=conv_id,
                metadata={"voice_duration_ms": duration_ms},
            )
            storage_url = result.get("url", "")
            file_size = result.get("size", len(content))
        else:
            # No media module - store as base64 in message metadata (fallback)
            import base64

            storage_url = f"data:{audio.content_type};base64,{base64.b64encode(content).decode('ascii')}"
            file_size = len(content)
    except Exception as e:
        logger.error(f"Failed to store voice message audio: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Failed to store audio file"}},
        )

    # Create voice message record
    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.voice import VoiceMessageService

    svc = VoiceMessageService(db)

    try:
        result = svc.create_voice_message(
            user_id=current_user.user_id,
            conversation_id=conv_id,
            duration_ms=duration_ms,
            filename=filename,
            content_type=audio.content_type,
            size=file_size,
            url=storage_url,
            waveform=waveform,
            participant_svc=participant_svc,
        )

        # Send the actual message via messaging module
        if messaging and result.get("attachment_data"):
            try:
                msg = messaging.send_message(
                    user_id=current_user.user_id,
                    conversation_id=conv_id,
                    content="🎤 Voice message",
                    attachments=[result["attachment_data"]],
                )
                result["message_id"] = msg.id
            except Exception as e:
                logger.error(f"Failed to send voice message via messaging: {e}")

        return {"success": True, "voice_message": result}
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )


# ==================== Channel Slowmode ====================


@router.put(
    "/channels/{channel_id}/slowmode",
    summary="Set channel slowmode",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def set_channel_slowmode(
    channel_id: str,
    body: ThreadSlowmodeRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Set slowmode for a channel. Requires manage channel permission."""
    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )

    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    # Convert interval_ms to seconds for channel slowmode
    interval_seconds = max(0, body.interval_ms // 1000)

    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    try:
        # Update channel slowmode in database
        db.execute(
            "UPDATE srv_channels SET slowmode_seconds = ? WHERE id = ?",
            (interval_seconds, cid),
        )
        logger.info(
            f"Channel {cid} slowmode set to {interval_seconds}s by user {current_user.user_id}"
        )
        return {
            "success": True,
            "channel_id": cid,
            "slowmode_seconds": interval_seconds,
        }
    except Exception as e:
        logger.error(f"Failed to set channel slowmode: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/channels/{channel_id}/slowmode",
    summary="Get channel slowmode",
    responses={401: {"model": ErrorResponse}},
)
async def get_channel_slowmode(
    channel_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    """Get slowmode settings for a channel."""
    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )

    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )
    row = db.fetch_one(
        "SELECT id, slowmode_seconds FROM srv_channels WHERE id = ?",
        (cid,),
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Channel not found"}},
        )

    return {"channel_id": cid, "slowmode_seconds": row["slowmode_seconds"] or 0}


# ==================== User Profiles ====================


@router.get(
    "/users/{user_id}/profile",
    summary="Get user profile",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_user_profile(
    user_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    """Get a user's profile (bio, status, social links, etc.)."""
    try:
        uid = int(user_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid user ID"}},
        )

    db = api.get_db()
    from src.core.profiles.manager import ProfileManager

    svc = ProfileManager(db)

    result = svc.get_profile(uid)
    if not result:
        # Return default profile
        return {"user_id": uid, "bio": None, "status": None, "social_links": []}
    return {"profile": result}


@router.patch(
    "/users/@me/profile",
    summary="Update your profile",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def update_own_profile(
    body: ProfileUpdateRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """Update your profile (bio, status, social links, etc.)."""
    db = api.get_db()
    from src.core.profiles.manager import ProfileManager

    svc = ProfileManager(db)

    try:
        result = svc.update_profile(
            user_id=current_user.user_id,
            bio=body.bio,
            pronouns=body.pronouns,
            location=body.location,
            timezone=body.timezone,
            banner_url=body.banner_url,
            social_links=body.social_links,
        )
        return {"success": True, "profile": result}
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )


# ==================== Push Tokens ====================


@router.post(
    "/push/tokens",
    summary="Register push notification token",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def register_push_token(
    body: PushTokenRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """Register a device token for mobile push notifications."""
    if body.platform not in ("ios", "android", "web"):
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid platform"}},
        )

    db = api.get_db()
    from src.core.push.manager import PushManager

    svc = PushManager(db)

    try:
        result = svc.register_token(
            user_id=current_user.user_id,
            token=body.token,
            platform=body.platform,
            device_id=body.device_id,
            app_version=body.app_version,
        )
        return {"success": True, "token_record": result}
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )


@router.delete(
    "/push/tokens/{token_id}",
    summary="Unregister push token",
    responses={401: {"model": ErrorResponse}},
)
async def unregister_push_token(
    token_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    """Remove a push notification token."""
    try:
        tid = int(token_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid token ID"}},
        )

    db = api.get_db()
    from src.core.push.manager import PushManager

    svc = PushManager(db)
    svc.unregister_token(current_user.user_id, str(tid))
    return {"success": True}


# ==================== Last Chat ====================


@router.put(
    "/users/@me/last-chat",
    summary="Save last active chat",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def save_last_chat(
    body: LastChatRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """Save your last active conversation for session restoration."""
    try:
        conversation_id = int(body.conversation_id)
        last_message_id = int(body.last_message_id) if body.last_message_id else None
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid ID format"}},
        )

    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.last_chat import LastChatService

    svc = LastChatService(db, participant_svc)

    try:
        result = svc.save_last_chat(
            user_id=current_user.user_id,
            conversation_id=conversation_id,
            last_message_id=last_message_id,
            scroll_position=body.scroll_position
            if body.scroll_position is not None
            else 0,
        )
        return {"success": True, "last_chat": result}
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )
    except Exception as e:
        # Log the full error for debugging
        logger.error(f"Failed to save last chat for user {current_user.user_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Failed to save last chat state"}
            },
        )


@router.get(
    "/users/@me/last-chat",
    summary="Get last active chat",
    responses={401: {"model": ErrorResponse}},
)
async def get_last_chat(
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get your last active conversation for session restoration."""
    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.last_chat import LastChatService

    svc = LastChatService(db, participant_svc)

    result = svc.get_last_chat(current_user.user_id)
    if not result:
        return {"last_chat": None}
    return {"last_chat": result}


@router.get(
    "/users/@me/recent-chats",
    summary="Get recent chat history",
    responses={401: {"model": ErrorResponse}},
)
async def get_recent_chats(
    limit: int = Query(10, ge=1, le=50),
    current_user: TokenInfo = Depends(get_current_user),
):
    """Get your recently accessed conversations."""
    db = api.get_db()
    messaging = api.get_messaging()
    participant_svc = (
        getattr(messaging, "_participant_svc", None) if messaging else None
    )

    from src.core.messaging.services.last_chat import LastChatService

    svc = LastChatService(db, participant_svc)

    results = svc.get_recent_chats(current_user.user_id, limit)
    return {"recent_chats": results}


# ==================== Thread Slowmode ====================


@router.put(
    "/threads/{thread_id}/slowmode",
    summary="Set thread slowmode",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def set_thread_slowmode(
    thread_id: str,
    body: ThreadSlowmodeRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Set slowmode for a thread. Requires manage thread permission."""
    try:
        tid = int(thread_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid thread ID"}},
        )

    db = api.get_db()
    from src.core.threads.slowmode import ThreadSlowmode

    # Check permission via thread manager
    threads = api.get_threads()
    if not threads:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Threads module not available"}},
        )

    can_manage = threads.can_manage_thread(current_user.user_id, tid)

    svc = ThreadSlowmode(db)
    try:
        result = svc.set_slowmode(
            tid, body.interval_ms, current_user.user_id, can_manage
        )
        return {"success": True, "slowmode": result}
    except PermissionError as e:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": str(e)}}
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400, detail={"error": {"code": 400, "message": str(e)}}
        )


@router.get(
    "/threads/{thread_id}/slowmode",
    summary="Get thread slowmode",
    responses={401: {"model": ErrorResponse}},
)
async def get_thread_slowmode(
    thread_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    """Get slowmode settings for a thread."""
    try:
        tid = int(thread_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid thread ID"}},
        )

    db = api.get_db()
    from src.core.threads.slowmode import ThreadSlowmode

    svc = ThreadSlowmode(db)
    result = svc.get_slowmode(tid)
    return {"slowmode": result}


# ==================== Audit Log (User-facing) ====================


class UserAuditLogQuery(BaseModel):
    server_id: Optional[str] = Field(None, description="Filter by server ID")
    action: Optional[str] = Field(None, description="Filter by action type")
    limit: int = Field(25, ge=1, le=100, description="Max entries to return")
    before: Optional[str] = Field(None, description="Entry ID to paginate before")


@router.get(
    "/users/@me/audit-logs",
    summary="Get your visible audit logs",
    responses={401: {"model": ErrorResponse}},
)
async def get_user_audit_logs(
    server_id: Optional[str] = None,
    action: Optional[str] = None,
    limit: int = Query(25, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
):
    """
    Get audit log entries visible to the current user.

    Returns entries from servers where the user has the view_audit_log permission.
    If server_id is provided, filters to that server only.
    """
    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Get servers the user is a member of
    try:
        servers = servers_mod.get_servers(current_user.user_id)
    except Exception:
        servers = []

    # Filter to servers where user has view_audit_log permission
    visible_server_ids = []
    for srv in servers or []:
        sid = getattr(srv, "id", None) or (
            srv.get("id") if isinstance(srv, dict) else None
        )
        if sid:
            try:
                perms = servers_mod.get_permissions(current_user.user_id, int(sid))
                if perms.get("server.view_audit_log", False):
                    visible_server_ids.append(int(sid))
            except Exception:
                continue

    if not visible_server_ids:
        return {"entries": []}

    # Filter to specific server if requested
    target_sid = None
    if server_id:
        try:
            target_sid = int(server_id)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid server ID"}},
            )
        if target_sid not in visible_server_ids:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": {
                        "code": 403,
                        "message": "No audit log access for this server",
                    }
                },
            )
        visible_server_ids = [target_sid]

    # Build query
    placeholders = ",".join("?" for _ in visible_server_ids)
    query = f"SELECT * FROM srv_audit_log WHERE server_id IN ({placeholders})"
    params: list = list(visible_server_ids)

    if action:
        query += " AND action = ?"
        params.append(action)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    try:
        rows = db.fetch_all(query, tuple(params))
        entries = []
        for row in rows:
            data = dict(row)
            entries.append(
                {
                    "id": data.get("id"),
                    "server_id": data.get("server_id"),
                    "user_id": data.get("user_id"),
                    "action": data.get("action"),
                    "target_type": data.get("target_type"),
                    "target_id": data.get("target_id"),
                    "changes": data.get("changes"),
                    "reason": data.get("reason"),
                    "created_at": data.get("created_at"),
                }
            )
        return {"entries": entries}
    except Exception as e:
        logger.error(f"Failed to get user audit logs: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Report Enhancement ====================


class EnhancedReportRequest(BaseModel):
    target_type: str = Field(..., description="'message' or 'user'")
    target_id: str = Field(..., description="ID of the reported message or user")
    reason: str = Field(..., min_length=1, max_length=1000, description="Report reason")
    category: str = Field("other", description="Report category")
    priority: str = Field("medium", description="Priority: low, medium, high, critical")
    details: Optional[str] = Field(
        None, max_length=2000, description="Additional details"
    )
    evidence_urls: Optional[List[str]] = Field(
        None, description="URLs of evidence (screenshots, etc.)"
    )
    channel_id: Optional[str] = Field(
        None, description="Channel ID (for message reports)"
    )
    server_id: Optional[str] = Field(
        None, description="Server ID (for message reports)"
    )


class ReportStatusUpdateRequest(BaseModel):
    status: str = Field(
        ...,
        description="New status: open, investigating, resolved, dismissed, escalated",
    )
    priority: Optional[str] = Field(None, description="Updated priority")
    assigned_to: Optional[str] = Field(None, description="Admin user ID to assign")
    admin_notes: Optional[str] = Field(None, max_length=2000, description="Admin notes")
    resolution: Optional[str] = Field(
        None, max_length=2000, description="Resolution description"
    )


@router.post(
    "/reports/enhanced",
    summary="Submit enhanced report",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def submit_enhanced_report(
    body: EnhancedReportRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """Submit a report with priority, evidence, and enhanced tracking."""
    # Validate priority
    valid_priorities = {"low", "medium", "high", "critical"}
    if body.priority not in valid_priorities:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Invalid priority. Must be one of: {', '.join(valid_priorities)}",
                }
            },
        )

    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    import json
    from src.utils.encryption import generate_snowflake_id
    import time

    try:
        target_id = int(body.target_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid target ID"}},
        )

    now = int(time.time() * 1000)
    report_id = generate_snowflake_id()

    # Get additional context based on target type
    reported_user_id = None
    message_content = None
    channel_id = None
    server_id = None

    if body.target_type == "message":
        channel_id = int(body.channel_id) if body.channel_id else None
        server_id = int(body.server_id) if body.server_id else None
        messaging = api.get_messaging()
        if messaging:
            try:
                msg = messaging.get_message(current_user.user_id, target_id)
                if msg:
                    reported_user_id = msg.author_id
                    message_content = msg.content[:500] if msg.content else None
            except Exception:
                pass
    elif body.target_type == "user":
        reported_user_id = target_id

    evidence_urls_str = json.dumps(body.evidence_urls) if body.evidence_urls else None

    try:
        db.execute(
            """INSERT INTO reports
               (id, reporter_id, report_type, target_id, channel_id, server_id,
                reason, category, details, evidence_ids, message_content,
                reported_user_id, status, priority, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
            (
                report_id,
                current_user.user_id,
                body.target_type,
                target_id,
                channel_id,
                server_id,
                body.reason,
                body.category,
                body.details,
                evidence_urls_str,
                message_content,
                reported_user_id,
                body.priority,
                now,
                now,
            ),
        )

        logger.info(
            f"Enhanced report {report_id} submitted by user {current_user.user_id}"
        )
        return {
            "success": True,
            "report_id": str(report_id),
            "status": "open",
            "priority": body.priority,
        }
    except Exception as e:
        logger.error(f"Failed to submit enhanced report: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/reports/{report_id}/status",
    summary="Update report status (admin)",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def update_report_status(
    report_id: str,
    body: ReportStatusUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Update the status of a report. Requires admin/moderator privileges."""
    # Verify admin status
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Check if user is admin
    admin_row = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?",
        (current_user.user_id,),
    )
    if not admin_row:
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Admin access required"}},
        )

    try:
        rid = int(report_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid report ID"}},
        )

    valid_statuses = {"open", "investigating", "resolved", "dismissed", "escalated"}
    if body.status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Invalid status. Must be one of: {', '.join(valid_statuses)}",
                }
            },
        )

    import time

    now = int(time.time() * 1000)

    updates = ["status = ?", "updated_at = ?"]
    params: list = [body.status, now]

    # Whitelist of allowed column names for UPDATE (prevents SQL injection)
    ALLOWED_UPDATE_COLUMNS = {
        "status",
        "updated_at",
        "priority",
        "assigned_to",
        "admin_notes",
        "resolution",
        "resolved_at",
        "resolved_by",
        "escalated_at",
        "evidence_urls",
        "reviewed_at",
        "reviewed_by",
    }

    if body.priority:
        updates.append("priority = ?")
        params.append(body.priority)
    if body.assigned_to:
        updates.append("assigned_to = ?")
        params.append(int(body.assigned_to))
    if body.admin_notes:
        updates.append("admin_notes = ?")
        params.append(body.admin_notes)
    if body.resolution:
        updates.append("resolution = ?")
        params.append(body.resolution)
        updates.append("resolved_at = ?")
        params.append(now)
        updates.append("resolved_by = ?")
        params.append(current_user.user_id)
    if body.status == "escalated":
        updates.append("escalated_at = ?")
        params.append(now)

    # Set reviewed_at/reviewed_by when status changes from 'open' to a reviewed status
    if body.status in ("investigating", "resolved", "dismissed", "escalated"):
        updates.append("reviewed_at = ?")
        params.append(now)
        updates.append("reviewed_by = ?")
        params.append(current_user.user_id)

    # Validate all column names before building query
    for u in updates:
        col = u.split(" = ")[0].strip()
        if col not in ALLOWED_UPDATE_COLUMNS:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": f"Invalid column: {col}"}},
            )

    params.append(rid)

    try:
        db.execute(
            f"UPDATE reports SET {', '.join(updates)} WHERE id = ?",
            tuple(params),
        )
        return {"success": True, "report_id": str(rid), "status": body.status}
    except Exception as e:
        logger.error(f"Failed to update report status: {e}")
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/reports/{report_id}",
    summary="Get report details",
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_report_details(
    report_id: str, current_user: TokenInfo = Depends(get_current_user)
):
    """Get detailed information about a report."""
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    try:
        rid = int(report_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid report ID"}},
        )

    # Try the reports table first
    row = db.fetch_one("SELECT * FROM reports WHERE id = ?", (rid,))
    if not row:
        # Try message_reports
        row = db.fetch_one("SELECT * FROM message_reports WHERE id = ?", (rid,))
    if not row:
        # Try user_reports
        row = db.fetch_one("SELECT * FROM user_reports WHERE id = ?", (rid,))
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Report not found"}},
        )

    # Only allow the reporter or admin to view details
    data = dict(row)
    is_reporter = data.get("reporter_id") == current_user.user_id
    is_admin = db.fetch_one(
        "SELECT id FROM admin_users WHERE id = ?", (current_user.user_id,)
    )

    if not is_reporter and not is_admin:
        raise HTTPException(
            status_code=403, detail={"error": {"code": 403, "message": "Access denied"}}
        )

    return {"report": data}


# ==================== Onboarding Presets ====================


class OnboardingPresetRequest(BaseModel):
    server_id: str = Field(..., description="Server ID")
    preset: str = Field(
        ...,
        description="Preset name: community, gaming, education, business, open_source",
    )


ONBOARDING_PRESETS = {
    "community": {
        "description": "Welcome to our community! Get started by picking your interests.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Pick your interests",
                "required": False,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Check out the rules",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Say hello!",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
    "gaming": {
        "description": "Welcome, gamer! Choose your games and find your squad.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Choose your games",
                "required": False,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Read the server rules",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Find a team",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
    "education": {
        "description": "Welcome to the class! Set up your profile and get started.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Select your course",
                "required": True,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Read the syllabus",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Introduce yourself",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
    "business": {
        "description": "Welcome to the team! Set up your department and access.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Select your department",
                "required": True,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Read company guidelines",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Introduce yourself",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
    "open_source": {
        "description": "Welcome to the project! Get started by choosing your contribution areas.",
        "steps": [
            {
                "type": "select_roles",
                "title": "Choose your contribution area",
                "required": False,
                "options_key": "role_ids",
            },
            {
                "type": "visit_channel",
                "title": "Read the contributing guide",
                "required": True,
                "options_key": "channel_id",
            },
            {
                "type": "visit_channel",
                "title": "Find good first issues",
                "required": False,
                "options_key": "channel_id",
            },
        ],
    },
}


@router.get(
    "/onboarding/presets",
    summary="List onboarding presets",
    responses={401: {"model": ErrorResponse}},
)
async def list_onboarding_presets(
    current_user: TokenInfo = Depends(get_current_user),
):
    """List available onboarding wizard presets."""
    return {"presets": list(ONBOARDING_PRESETS.keys()), "details": ONBOARDING_PRESETS}


@router.post(
    "/onboarding/apply-preset",
    summary="Apply onboarding preset to server",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
    },
)
async def apply_onboarding_preset(
    body: OnboardingPresetRequest, current_user: TokenInfo = Depends(get_current_user)
):
    """
    Apply an onboarding preset to a server.

    Creates onboarding steps from the chosen preset template.
    Requires onboarding.manage permission.
    """
    if body.preset not in ONBOARDING_PRESETS:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Invalid preset. Available: {', '.join(ONBOARDING_PRESETS.keys())}",
                }
            },
        )

    try:
        server_id = int(body.server_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid server ID"}},
        )

    servers_mod = api.get_servers()
    if not servers_mod:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Servers module not available"}},
        )

    # Verify permission (server.manage OR onboarding.manage)
    has_perm = False
    try:
        servers_mod.require_permission(
            current_user.user_id, server_id, "onboarding.manage"
        )
        has_perm = True
    except Exception:
        pass
    if not has_perm:
        try:
            servers_mod.require_permission(
                current_user.user_id, server_id, "server.manage"
            )
            has_perm = True
        except Exception:
            pass
    if not has_perm:
        raise HTTPException(
            status_code=403,
            detail={
                "error": {
                    "code": 403,
                    "message": "Missing onboarding.manage or server.manage permission",
                }
            },
        )

    preset = ONBOARDING_PRESETS[body.preset]

    # Get database instance for OnboardingManager
    db = api.get_db()
    if not db:
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Database not available"}},
        )

    # Use the onboarding module directly (methods are on OnboardingManager, not servers_mod)
    from src.core.servers.onboarding import OnboardingManager

    onboarding_mgr = OnboardingManager(db, servers_mod)

    # Apply welcome screen from preset
    try:
        onboarding_mgr.set_welcome_screen(
            user_id=current_user.user_id,
            server_id=server_id,
            description=preset["description"],
            enabled=True,
        )
    except Exception as e:
        logger.debug(f"Failed to set welcome screen from preset: {e}")

    # Create onboarding steps from preset
    created_steps = []
    for step_template in preset["steps"]:
        try:
            from src.core.servers.models import OnboardingStepType

            step_type = OnboardingStepType(step_template["type"])
            step = onboarding_mgr.create_onboarding_step(
                user_id=current_user.user_id,
                server_id=server_id,
                step_type=step_type,
                title=step_template["title"],
                required=step_template.get("required", False),
            )
            created_steps.append(
                {"id": str(step.id), "title": step.title, "type": step.step_type.value}
            )
        except Exception as e:
            logger.warning(f"Failed to create onboarding step from preset: {e}")
            created_steps.append(
                {
                    "title": step_template["title"],
                    "type": step_template["type"],
                    "error": str(e),
                }
            )

    logger.info(
        f"Applied onboarding preset '{body.preset}' to server {server_id} by user {current_user.user_id}"
    )
    return {
        "success": True,
        "preset": body.preset,
        "welcome_screen": {"description": preset["description"]},
        "steps_created": created_steps,
    }
