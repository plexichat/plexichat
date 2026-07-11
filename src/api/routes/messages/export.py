"""
Message export routes - Chat transcript export endpoints.
"""

from fastapi import APIRouter, HTTPException, Depends
from starlette.responses import FileResponse

import src.api as api
import utils.logger as logger
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.messages import (
    TranscriptExportRequest,
    TranscriptExportResponse,
    TranscriptExportStatusResponse,
)
from src.api.schemas.common import ErrorResponse

router = APIRouter(tags=["Messages"])


@router.post(
    "/channels/{channel_id}/messages/export",
    response_model=TranscriptExportResponse,
    summary="Export chat transcript",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request"},
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        403: {"model": ErrorResponse, "description": "Export not allowed"},
        404: {"model": ErrorResponse, "description": "Channel not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def request_transcript_export(
    channel_id: str,
    body: TranscriptExportRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> TranscriptExportResponse:
    """Request a chat transcript export for a channel."""
    msg_manager = api.get_messaging()
    servers_mod = api.get_servers()

    if not msg_manager:
        raise HTTPException(
            status_code=500,
            detail={
                "error": {"code": 500, "message": "Messaging module not available"}
            },
        )

    try:
        cid = int(channel_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid channel ID"}},
        )

    conv_id = cid

    # Check permissions
    if servers_mod:
        try:
            channel = servers_mod.get_channel(cid, current_user.user_id)
            if channel:
                # DM/Group DM channels - export disabled
                channel_type = getattr(channel, "channel_type", None)
                channel_type_str = (
                    channel_type.value
                    if channel_type and hasattr(channel_type, "value")
                    else str(channel_type)
                )
                if channel_type_str in ("dm", "group_dm"):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Transcript export is not available in direct messages",
                            }
                        },
                    )

                # Check channel-level export setting
                if not getattr(channel, "transcript_export_enabled", True):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "Transcript export is disabled for this channel",
                            }
                        },
                    )

                # Check user permission
                permissions = servers_mod.get_permissions(
                    current_user.user_id, channel.server_id, cid
                )
                if not permissions.get(
                    "messages.export_transcript"
                ) and not permissions.get("administrator"):
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "You do not have permission to export transcripts",
                            }
                        },
                    )

                if hasattr(channel, "conversation_id") and channel.conversation_id:
                    conv_id = channel.conversation_id
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Channel not found"}},
            )

    # Check export is enabled
    import utils.config as config

    export_config = config.get("transcript_export", {})
    if not export_config.get("enabled", True):
        raise HTTPException(
            status_code=403,
            detail={"error": {"code": 403, "message": "Transcript export is disabled"}},
        )

    # Check format is allowed
    allowed_formats = export_config.get(
        "allowed_formats", ["json", "csv", "txt", "html"]
    )
    if body.format not in allowed_formats:
        raise HTTPException(
            status_code=400,
            detail={
                "error": {
                    "code": 400,
                    "message": f"Format '{body.format}' not allowed. Allowed: {', '.join(allowed_formats)}",
                }
            },
        )

    try:
        from starlette.concurrency import run_in_threadpool

        def _request_export(uid, cid, fmt, frm, to, tz):
            from src.core.messaging.services.export import TranscriptExportService

            db = api.get_db()
            try:
                from src.core.messaging.repositories.message import MessageRepository
                from src.core.messaging.repositories.participant import (
                    ParticipantRepository,
                )

                svc = TranscriptExportService(
                    db, MessageRepository(db), ParticipantRepository(db)
                )
                return svc.request_export(uid, cid, fmt, frm, to, tz)
            finally:
                if db:
                    db.close()

        result = await run_in_threadpool(
            _request_export,
            current_user.user_id,
            conv_id,
            body.format,
            body.from_date,
            body.to_date,
            body.timezone,
        )

        return TranscriptExportResponse(
            export_id=result["export_id"],
            status=result["status"],
            message_count=result.get("message_count", 0),
            file_url=result.get("file_url"),
            expires_at=result.get("expires_at"),
            error=result.get("error"),
        )
    except Exception as e:
        logger.error(f"Export request failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/channels/{channel_id}/messages/export/{export_id}",
    response_model=TranscriptExportStatusResponse,
    summary="Get export status",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Export not found"},
    },
)
async def get_export_status(
    channel_id: str,
    export_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> TranscriptExportStatusResponse:
    """Get the status of a transcript export request."""
    try:
        db = api.get_db()
        try:
            from src.core.messaging.services.export import TranscriptExportService
            from src.core.messaging.repositories.message import MessageRepository
            from src.core.messaging.repositories.participant import (
                ParticipantRepository,
            )

            svc = TranscriptExportService(
                db, MessageRepository(db), ParticipantRepository(db)
            )
            status = svc.get_export_status(export_id)
        finally:
            if db:
                db.close()
    except Exception as e:
        logger.error(f"Export status check failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    if not status:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Export not found"}},
        )

    # Ownership check
    if str(status.get("user_id")) != str(current_user.user_id):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Export not found"}},
        )

    return TranscriptExportStatusResponse(
        export_id=status["export_id"],
        status=status["status"],
        message_count=status.get("message_count", 0),
        file_url=status.get("file_url"),
        expires_at=status.get("expires_at"),
        error=status.get("error"),
    )


@router.get(
    "/channels/{channel_id}/messages/export/{export_id}/download",
    summary="Download transcript export",
    responses={
        401: {"model": ErrorResponse, "description": "Not authenticated"},
        404: {"model": ErrorResponse, "description": "Export not found or expired"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def download_transcript_export(
    channel_id: str,
    export_id: str,
    current_user: TokenInfo = Depends(get_current_user),
):
    """Download a completed transcript export."""
    try:
        db = api.get_db()
        try:
            from src.core.messaging.services.export import TranscriptExportService
            from src.core.messaging.repositories.message import MessageRepository
            from src.core.messaging.repositories.participant import (
                ParticipantRepository,
            )

            svc = TranscriptExportService(
                db, MessageRepository(db), ParticipantRepository(db)
            )
            file_path = svc.get_export_file_path(export_id, str(current_user.user_id))
            status = svc.get_export_status(export_id)
        finally:
            if db:
                db.close()
    except Exception as e:
        logger.error(f"Export download failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )

    if not file_path or not status:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Export not found"}},
        )

    # Ownership check
    if str(status.get("user_id")) != str(current_user.user_id):
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Export not found"}},
        )

    if status.get("expires_at", 0) < __import__("time").time():
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": 404, "message": "Export has expired"}},
        )

    filename = f"transcript_{channel_id}.{status.get('format', 'json')}"
    return FileResponse(
        file_path,
        media_type=status.get("mime_type", "application/octet-stream"),
        filename=filename,
    )
