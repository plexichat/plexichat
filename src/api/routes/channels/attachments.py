from fastapi import HTTPException, UploadFile, Depends
from starlette.concurrency import run_in_threadpool

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.channels import AttachmentUploadResponse
from src.api.schemas.common import ErrorResponse

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
                404: {"model": ErrorResponse, "description": "Channel not found"},
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

            has_access = False

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
                logger.warning(
                    f"User {current_user.user_id} denied access to channel {cid} for attachment upload"
                )
                raise HTTPException(
                    status_code=404,
                    detail={"error": {"code": 404, "message": "Channel not found"}},
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
            except Exception as e:
                exc_name = type(e).__name__
                if "Size" in exc_name:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                elif "Type" in exc_name:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )
                elif "Blocked" in exc_name or "Malware" in exc_name:
                    logger.warning(
                        f"File upload blocked for user {current_user.user_id}: {e}"
                    )
                    raise HTTPException(
                        status_code=400,
                        detail={"error": {"code": 400, "message": str(e)}},
                    )

                logger.error(
                    f"Attachment upload failed for channel {cid}: {e}", exc_info=True
                )
                raise HTTPException(
                    status_code=500,
                    detail={"error": {"code": 500, "message": "Upload failed"}},
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(
                f"Unexpected error in upload_attachment for {channel_id}: {e}",
                exc_info=True,
            )
            raise HTTPException(
                status_code=500, detail={"error": {"code": 500, "message": str(e)}}
            )
