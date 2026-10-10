from fastapi import HTTPException, UploadFile, Depends
from starlette.concurrency import run_in_threadpool

import src.api as api
from src.api.routes.channels.helpers import _get_upload_limit
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.channels import AttachmentUploadResponse
from src.api.schemas.common import ErrorResponse
from src.core.media.exceptions import (
    FileSizeError,
    FileTypeError,
    MalwareDetectedError,
    ScannerUnavailableError,
)

import utils.logger as logger

from .base import ChannelBase


class ChannelAttachmentsMixin(ChannelBase):
    def _register_routes(self) -> None:
        self.router.add_api_route(
            "/{channel_id}/attachments",
            self._upload_attachment,
            methods=["POST"],
            response_model=AttachmentUploadResponse,
            summary="Upload attachment",
            responses={
                400: {
                    "model": ErrorResponse,
                    "description": "Invalid channel ID or file too large/unsupported",
                },
                401: {"model": ErrorResponse, "description": "Not authenticated"},
                403: {
                    "model": ErrorResponse,
                    "description": "No send/upload permission in channel",
                },
                404: {"model": ErrorResponse, "description": "Channel not found"},
                413: {
                    "model": ErrorResponse,
                    "description": "File exceeds the account tier upload limit",
                },
                500: {"model": ErrorResponse, "description": "Internal server error"},
            },
        )

    async def _upload_attachment(
        self,
        channel_id: str,
        file: UploadFile,
        current_user: TokenInfo = Depends(get_current_user),
    ) -> AttachmentUploadResponse:
        servers_mod = api.get_servers()
        messaging = api.get_messaging()
        media = api.get_media()

        if not servers_mod:
            logger.error("Servers module not available for attachment upload")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Servers module not available"}
                },
            )

        if not media:
            logger.error("Media module not available for attachment upload")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {"code": 500, "message": "Media module not available"}
                },
            )

        try:
            try:
                cid = int(channel_id)
            except (ValueError, TypeError):
                logger.warning(
                    f"Invalid channel ID format for attachment: {channel_id}"
                )
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid channel ID"}},
                )

            # SECURITY (tier-cap parity): the client pre-checks the tier cap
            # at file selection, but a crafted client can skip that. Enforce
            # the same per-user limit server-side BEFORE probing channel
            # access or spooling the body, so low-tier accounts cannot
            # bypass their quota with raw HTTP.
            upload_limit = _get_upload_limit(current_user.user_id)
            if file.size is not None and file.size > upload_limit:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "error": {
                            "code": 413,
                            "message": "File exceeds the maximum upload size for your account",
                        }
                    },
                )

            has_access = False
            channel = None

            if servers_mod:
                try:
                    channel = servers_mod.get_channel(cid, current_user.user_id)
                    if channel:
                        has_access = True
                except Exception:
                    pass

            if not has_access and messaging:
                try:
                    conv = messaging.get_conversation(cid, current_user.user_id)
                    if conv:
                        has_access = True
                except Exception:
                    pass

            if not has_access:
                # Dual probe: 404 only when the channel genuinely does not
                # exist; 403 when it exists but the caller is blocked.
                exists = False
                if servers_mod and channel is None:
                    try:
                        exists = bool(servers_mod.channel_exists(cid))
                    except Exception:
                        exists = False
                if not exists and messaging:
                    try:
                        exists = bool(messaging.conversation_exists(cid))
                    except Exception:
                        exists = False
                if not exists:
                    logger.warning(
                        f"User {current_user.user_id} uploaded attachment to "
                        f"nonexistent channel {cid}"
                    )
                    raise HTTPException(
                        status_code=404,
                        detail={"error": {"code": 404, "message": "Channel not found"}},
                    )
                logger.warning(
                    f"User {current_user.user_id} denied access to channel {cid} for attachment upload"
                )
                raise HTTPException(
                    status_code=403,
                    detail={"error": {"code": 403, "message": "Channel access denied"}},
                )

            # Permission tier: membership alone is not enough - uploading
            # consumes storage quota, so require the send/upload permission.
            if channel is not None and getattr(channel, "server_id", None):
                try:
                    allowed = servers_mod.has_permission(
                        current_user.user_id,
                        channel.server_id,
                        "messages.send",
                        cid,
                    )
                except Exception as perm_exc:
                    logger.warning(
                        f"Permission check failed for attachment upload in "
                        f"channel {cid}: {perm_exc}"
                    )
                    allowed = False
                if not allowed:
                    logger.warning(
                        f"User {current_user.user_id} lacks messages.send "
                        f"permission in channel {cid} (attachment upload denied)"
                    )
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "error": {
                                "code": 403,
                                "message": "You do not have permission to upload files in this channel",
                            }
                        },
                    )

            try:

                def _upload_with_cleanup():
                    import src.api as api_module

                    db = api_module.get_db()
                    try:
                        stream = file.file
                        stream.seek(0)
                        return media.upload_stream(
                            user_id=current_user.user_id,
                            stream=stream,
                            filename=file.filename or "attachment",
                            content_type=file.content_type
                            or "application/octet-stream",
                            size=file.size or 0,
                        )
                    finally:
                        if db:
                            db.close()

                result = await run_in_threadpool(_upload_with_cleanup)

                thumbnails_str = (
                    {str(k): v for k, v in result.thumbnails.items()}
                    if result.thumbnails
                    else None
                )
                return AttachmentUploadResponse(
                    id=str(result.file_id),
                    filename=result.filename,
                    size=result.size,
                    content_type=result.content_type,
                    url=result.url,
                    hash=result.checksum,
                    thumbnails=thumbnails_str,
                )
            except HTTPException:
                raise
            except (FileSizeError, FileTypeError) as e:
                logger.warning(
                    f"Attachment rejected for user {current_user.user_id} in "
                    f"channel {cid}: {e}"
                )
                raise HTTPException(
                    status_code=400,
                    detail={"error": {"code": 400, "message": "Invalid request"}},
                )
            except (MalwareDetectedError, ScannerUnavailableError) as e:
                logger.warning(
                    f"File upload blocked for user {current_user.user_id}: {e}"
                )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "File rejected by security scan",
                        }
                    },
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in upload_attachment for {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "error": {
                        "code": 500,
                        "message": "Internal server error",
                    }
                },
            )
