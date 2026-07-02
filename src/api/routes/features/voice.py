from fastapi import APIRouter, Depends, UploadFile, File, Form
from pydantic import BaseModel, Field
from typing import Optional

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse
from .common import parse_id, raise_bad_request, raise_internal

router = APIRouter()


class VoiceMessageRequest(BaseModel):
    conversation_id: str = Field(..., description="Target conversation ID")
    duration_ms: int = Field(..., gt=0, description="Duration in milliseconds")
    filename: str = Field(..., min_length=1, description="Audio filename")
    content_type: str = Field(..., description="MIME type (audio/*)")
    size: int = Field(..., gt=0, description="File size in bytes")
    url: str = Field(..., description="Storage URL")
    waveform: Optional[str] = Field(None, description="Base64 waveform data")


@router.post(
    "/voice-messages",
    summary="Send a voice message",
    responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
)
async def send_voice_message(
    body: VoiceMessageRequest, current_user: TokenInfo = Depends(get_current_user)
):
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

        attachment_data = getattr(result, "attachment_data", None)
        if attachment_data is None:
            attachment_data = {
                "filename": result.get("filename")
                if isinstance(result, dict)
                else result.filename,
                "content_type": result.get("content_type")
                if isinstance(result, dict)
                else result.content_type,
                "size": result.get("size") if isinstance(result, dict) else result.size,
                "url": result.get("url") if isinstance(result, dict) else result.url,
                "metadata": result.get("metadata")
                if isinstance(result, dict)
                else result.metadata,
            }

        if messaging:
            try:
                msg = messaging.send_message(
                    user_id=current_user.user_id,
                    conversation_id=int(body.conversation_id),
                    content="Voice message",
                    attachments=[attachment_data],
                )
                if isinstance(result, dict):
                    result["message_id"] = msg.id
                else:
                    setattr(result, "message_id", msg.id)
            except Exception as e:
                logger.error(f"Failed to send voice message via messaging: {e}")

        return {"success": True, "voice_message": result}
    except ValueError as e:
        raise_bad_request(str(e))


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
        raise_bad_request(
            f"Invalid content type '{audio.content_type}'. "
            f"Allowed: {', '.join(sorted(allowed_content_types))}"
        )

    MAX_UPLOAD_SIZE = 10 * 1024 * 1024
    content = await audio.read()
    if len(content) > MAX_UPLOAD_SIZE:
        raise_bad_request("Audio file exceeds 10MB maximum size")
    if len(content) == 0:
        raise_bad_request("Audio file is empty")

    MAX_DURATION_MS = 600000
    if duration_ms > MAX_DURATION_MS:
        raise_bad_request("Voice message exceeds 10 minute maximum duration")

    conv_id = parse_id(conversation_id, "conversation ID")

    import time

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
        filename = f"voice_{current_user.user_id}_{int(time.time() * 1000)}.{ext}"

        if media:
            result = media.upload_file(
                file_data=content,
                filename=filename,
                content_type=audio.content_type,
                user_id=current_user.user_id,
            )
            attachment_data = {
                "filename": result.get("filename")
                if isinstance(result, dict)
                else result.filename,
                "content_type": result.get("content_type")
                if isinstance(result, dict)
                else result.content_type,
                "size": result.get("size") if isinstance(result, dict) else result.size,
                "url": result.get("url") if isinstance(result, dict) else result.url,
                "metadata": result.get("metadata")
                if isinstance(result, dict)
                else result.metadata,
            }
            storage_url = attachment_data["url"] if attachment_data["url"] else ""
            file_size = (
                attachment_data["size"] if attachment_data["size"] else len(content)
            )
        else:
            import base64

            storage_url = f"data:{audio.content_type};base64,{base64.b64encode(content).decode('ascii')}"
            file_size = len(content)
    except Exception as e:
        logger.error(f"Failed to store voice message audio: {e}")
        raise_internal("Failed to store audio file")

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

        attachment_data = (
            result.get("attachment_data")
            if isinstance(result, dict)
            else getattr(result, "attachment_data", None)
        )
        if messaging and attachment_data:
            try:
                msg = messaging.send_message(
                    user_id=current_user.user_id,
                    conversation_id=conv_id,
                    content="Voice message",
                    attachments=[attachment_data],
                )
                if isinstance(result, dict):
                    result["message_id"] = msg.id
                else:
                    setattr(result, "message_id", msg.id)
            except Exception as e:
                logger.error(f"Failed to send voice message via messaging: {e}")
        return {"success": True, "voice_message": result}
    except ValueError as e:
        raise_bad_request(str(e))
    except PermissionError as e:
        raise_internal(str(e))
