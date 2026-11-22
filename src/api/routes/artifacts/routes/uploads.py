"""Artifact conversion of an existing chat upload."""

from __future__ import annotations

import json
from typing import Any, Dict

import utils.config as config
import utils.logger as logger
import src.api as api
from fastapi import APIRouter, Depends, HTTPException, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.artifacts import ArtifactResponse, ConvertUploadRequest
from src.api.schemas.common import ErrorResponse
from src.core.artifacts.models import ArtifactType
from src.core.security.hardening import bounded_int

from ..validation import parse_snowflake_id
from ...artifacts_helpers import (
    _AuthCache,
    _artifact_response,
    _authorize_scope,
    _emit_artifact_message,
    _get_manager,
    _is_conversation_member,
    _require_artifact_capability,
    _require_server_permission,
    _validate_artifact_scope,
)

router = APIRouter(prefix="/artifacts", tags=["Artifacts"])


@router.post(
    "/convert-upload",
    response_model=ArtifactResponse,
    summary="Convert an upload to an artifact",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Attachment or message not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def _convert_upload_impl(
    body: ConvertUploadRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Convert an existing attachment into an artifact."""
    # A bot token's user_id is its human owner. Without this gate a narrow
    # bot credential wields the owner's server/conversation authority through
    # _authorize_scope and authors the converted row as the owner.
    require_user_token(current_user)
    try:
        conversation_id = (
            int(body.conversation_id) if body.conversation_id is not None else None
        )
        channel_id = int(body.channel_id) if body.channel_id is not None else None
        server_id = int(body.server_id) if body.server_id is not None else None

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Database unavailable"}},
            )

        attachment_id = parse_snowflake_id(body.attachment_id, "attachment ID")
        if attachment_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid attachment ID"}},
            )
        row = db.fetch_one(
            "SELECT * FROM msg_attachments WHERE id = ? AND deleted = 0",
            (attachment_id,),
        )
        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Attachment not found"}},
            )

        attachment = dict(row)
        attachment["attachment_id"] = attachment_id

        max_size_mb = bounded_int(
            config.get("artifacts", {}).get("max_artifact_size_mb", 200),
            "max_artifact_size_mb",
            minimum=1,
            maximum=10240,
        )
        max_size_bytes = max_size_mb * 1024 * 1024
        attachment_size = bounded_int(
            attachment.get("size", 0) or 0,
            "attachment size",
            minimum=0,
            maximum=2**63 - 1,
        )
        if attachment_size > max_size_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": f"Attachment exceeds maximum size of {max_size_mb} MB",
                    }
                },
            )

        msg_row = db.fetch_one(
            "SELECT conversation_id, author_id FROM msg_messages WHERE id = ? AND deleted = 0",
            (attachment["message_id"],),
        )
        if not msg_row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Source message not found"}},
            )
        msg_data = dict(msg_row)

        if current_user.user_id != msg_data["author_id"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Not authorized"}},
            )

        source_conv_id = msg_data["conversation_id"]
        messaging_mod = api.get_messaging()
        auth_cache = _AuthCache()
        if source_conv_id is not None and not _is_conversation_member(
            messaging_mod, source_conv_id, current_user.user_id, auth_cache
        ):
            # Conversation non-members may still convert the upload if they hold
            # the server-level ``artifact.create`` permission (e.g. a server
            # admin acting on a channel they don't personally participate in).
            if not _require_server_permission(
                current_user.user_id, server_id, "artifact.create", auth_cache
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": "Not authorized"}},
                )

        # The artifact must remain attached to the source message's
        # conversation and its server/channel scope. Accepting arbitrary target
        # IDs lets a caller move an attachment across conversations.
        source_conv_id = msg_data.get("conversation_id")
        if source_conv_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Source message has no conversation",
                    }
                },
            )
        source_conv_id = int(source_conv_id)
        if conversation_id is not None and conversation_id != source_conv_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "conversation_id must match the source message",
                    }
                },
            )
        conversation_row = db.fetch_one(
            "SELECT metadata FROM msg_conversations WHERE id = ?",
            (source_conv_id,),
        )
        conversation_metadata: Dict[str, Any] = {}
        if conversation_row and conversation_row.get("metadata"):
            try:
                parsed_metadata = json.loads(conversation_row["metadata"])
                if isinstance(parsed_metadata, dict):
                    conversation_metadata = parsed_metadata
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail={
                        "error": {
                            "code": 500,
                            "message": "Source conversation metadata is invalid",
                        }
                    },
                )
        source_server_id = conversation_metadata.get("server_id")
        source_channel_id = conversation_metadata.get("channel_id")
        if (
            server_id is not None
            and source_server_id is not None
            and int(server_id) != int(source_server_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "server_id must match the source conversation",
                    }
                },
            )
        if (
            channel_id is not None
            and source_channel_id is not None
            and int(channel_id) != int(source_channel_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "channel_id must match the source conversation",
                    }
                },
            )
        conversation_id = source_conv_id
        server_id = int(source_server_id) if source_server_id is not None else server_id
        channel_id = (
            int(source_channel_id) if source_channel_id is not None else channel_id
        )
        conversation_id, channel_id, server_id = _validate_artifact_scope(
            conversation_id, channel_id, server_id, auth_cache
        )
        # Re-run authorization against the actual source scope after deriving
        # it from the database, rather than authorizing an omitted target scope.
        _authorize_scope(
            current_user.user_id,
            conversation_id,
            server_id,
            "artifact.create",
            author_id=current_user.user_id,
            cache=auth_cache,
        )

        manager = _get_manager()
        _require_artifact_capability(ArtifactType.UPLOAD)
        artifact = manager.convert_upload_to_artifact(
            attachment=attachment,
            conversation_id=conversation_id,
            author_id=current_user.user_id,
            title=body.title,
            server_id=server_id,
            channel_id=channel_id,
        )

        author = {
            "id": str(current_user.user_id),
            "username": current_user.username,
        }
        _emit_artifact_message(ArtifactResponse.model_validate(artifact), author)

        return _artifact_response(current_user.user_id, artifact, auth_cache=auth_cache)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to convert upload: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
