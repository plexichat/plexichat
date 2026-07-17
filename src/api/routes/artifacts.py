"""
Artifact routes - REST API for the Artifacts feature.

Implements CRUD + listing for artifacts plus inline transcript emission.
Permission checks defer to the server RBAC layer (`artifact.view/create/
edit/delete/manage_retention`); for DM/group conversations (no server) the
caller must be a participant/owner of the conversation.
"""

from typing import Any, Dict, List, Optional

import utils.logger as logger
import utils.config as config
from fastapi import APIRouter, HTTPException, Depends, status

import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.artifacts.models import ArtifactType, ArtifactStatus
from src.api.schemas.artifacts import (
    ArtifactCreateRequest,
    ArtifactUpdateRequest,
    ArtifactResponse,
    ArtifactListResponse,
    ConvertUploadRequest,
)

router = APIRouter(prefix="/artifacts", tags=["Artifacts"])

ARTIFACT_ICON = config.get("artifacts", {}).get("inline_icon", "📎")

# === Permission helpers ===


def _require_server_permission(
    user_id: int, server_id: Optional[int], permission: str
) -> bool:
    """Return True if the user holds ``permission`` in ``server_id``.

    Treats a missing server module or unknown permission name as a deny rather
    than an error, which keeps the route usable before RBAC config is wired.
    """
    if server_id is None:
        return False
    servers_mod = api.get_servers()
    if servers_mod is None:
        return False
    from src.core.servers.exceptions import PermissionDeniedError

    try:
        servers_mod.require_permission(user_id, server_id, permission)
        return True
    except PermissionDeniedError:
        return False
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"server permission check failed for {permission}: {e}")
        return False


def _is_conversation_member(
    messaging_mod: Any, conversation_id: Optional[int], user_id: int
) -> bool:
    """Return True if ``user_id`` participates in / owns the conversation."""
    if conversation_id is None or messaging_mod is None:
        return False
    try:
        return messaging_mod.is_participant(conversation_id, user_id)
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"membership check failed for conv {conversation_id}: {e}")
        return False


def _authorize_scope(
    user_id: int,
    conversation_id: Optional[int],
    server_id: Optional[int],
    permission: str,
) -> None:
    """Authorize a server-scoped or conversation-scoped action.

    Raises HTTPException(403) when the caller has neither the server permission
    nor conversation membership.
    """
    if _require_server_permission(user_id, server_id, permission):
        return
    messaging_mod = api.get_messaging()
    if _is_conversation_member(messaging_mod, conversation_id, user_id):
        return
    if server_id is None and conversation_id is None:
        # Personal / notes-style scope: only the author may act. Callers that
        # already validated author_id should pass it through; here we deny.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": {"code": 403, "message": "Not authorized"}},
        )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": {"code": 403, "message": "Not authorized"}},
    )


# === Manager access ===


def _get_manager():
    db = api.get_db()
    if db is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Database unavailable"}},
        )
    from src.core.artifacts.manager import ArtifactManager

    artifacts_cfg = config.get("artifacts", {}) or {}
    return ArtifactManager(db, artifacts_cfg)


# === Inline message emission ===


def _emit_artifact_message(
    artifact: ArtifactResponse,
    author: Dict[str, Any],
) -> None:
    """Emit a MESSAGE_CREATE so the artifact appears in transcript history.

    The message references the artifact via ``metadata.artifact_id`` and uses
    the ``artifact`` message type. Delivery is scoped to the conversation
    participants (preferred) or the owning server; failures are swallowed so
    the underlying artifact write is never rolled back.
    """
    try:
        from src.core import events

        if not events.is_setup():
            return

        server_id = artifact.server_id
        channel_id = artifact.channel_id
        conversation_id = artifact.conversation_id

        content = f"{ARTIFACT_ICON} {artifact.title}"
        event = events.create_message_create(
            message_id=int(artifact.id),
            channel_id=int(channel_id) if channel_id else 0,
            author_id=int(artifact.author_id),
            content=content,
            server_id=int(server_id) if server_id else None,
            author=author,
        )
        event.data["type"] = 0
        event.data["message_type"] = "artifact"
        event.data["metadata"] = {"artifact_id": str(artifact.id)}

        user_ids: Optional[List[int]] = None
        messaging_mod = api.get_messaging()
        if conversation_id is not None and messaging_mod is not None:
            try:
                user_ids = [
                    int(u) for u in messaging_mod.get_participant_ids(conversation_id)
                ]
            except Exception as e:  # pragma: no cover - defensive
                logger.debug(f"Failed to resolve participants: {e}")
                user_ids = None

        events.dispatch(
            event,
            user_ids=user_ids,
            server_id=int(server_id) if server_id else None,
            channel_id=int(channel_id) if channel_id else None,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.debug(f"Failed to emit artifact message: {e}")


# === Routes ===


@router.post(
    "",
    response_model=ArtifactResponse,
    summary="Create an artifact",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_artifact(
    body: ArtifactCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Create a new artifact and emit an inline transcript message."""
    try:
        conversation_id = (
            int(body.conversation_id) if body.conversation_id is not None else None
        )
        channel_id = int(body.channel_id) if body.channel_id is not None else None
        server_id = int(body.server_id) if body.server_id is not None else None

        _authorize_scope(
            current_user.user_id, conversation_id, server_id, "artifact.create"
        )

        manager = _get_manager()
        artifact_type = ArtifactType(body.artifact_type.value)
        status_enum = ArtifactStatus((body.status or ArtifactStatus.COMPLETED).value)

        artifact = manager.create(
            conversation_id=conversation_id,
            author_id=current_user.user_id,
            artifact_type=artifact_type,
            title=body.title,
            summary=body.summary,
            channel_id=channel_id,
            server_id=server_id,
            status=status_enum,
            recorded=body.recorded,
            has_transcript=body.has_transcript,
            payload=body.payload,
            retention_policy=body.retention_policy,
            license_feature=body.license_feature,
        )

        author = {
            "id": str(current_user.user_id),
            "username": current_user.username,
        }
        _emit_artifact_message(ArtifactResponse.model_validate(artifact), author)

        return ArtifactResponse.model_validate(artifact)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to create artifact: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "",
    response_model=ArtifactListResponse,
    summary="List artifacts",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_artifacts(
    conversation_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    server_id: Optional[str] = None,
    author_id: Optional[str] = None,
    artifact_type: Optional[str] = None,
    status_filter: Optional[str] = None,
    recorded: Optional[bool] = None,
    has_transcript: Optional[bool] = None,
    search: Optional[str] = None,
    sort_by: str = "created_at",
    sort_order: str = "desc",
    limit: int = 50,
    offset: int = 0,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactListResponse:
    """List artifacts with query filters."""
    try:
        conv_id = int(conversation_id) if conversation_id is not None else None
        chan_id = int(channel_id) if channel_id is not None else None
        srv_id = int(server_id) if server_id is not None else None
        auth_id = int(author_id) if author_id is not None else None

        _authorize_scope(current_user.user_id, conv_id, srv_id, "artifact.view")

        manager = _get_manager()

        types: Optional[List[Any]] = None
        if artifact_type:
            parts = [p.strip() for p in artifact_type.split(",") if p.strip()]
            types = []
            valid_types = {t.value for t in ArtifactType}
            for p in parts:
                if p in valid_types:
                    types.append(ArtifactType(p))
            # Invalid type values are ignored rather than rejected: a list
            # filter should narrow results, not hard-fail on an unknown label.

        filters: Dict[str, Any] = {
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
        }
        if types is not None:
            filters["artifact_type"] = types
        if status_filter is not None:
            valid_statuses = {s.value for s in ArtifactStatus}
            if status_filter in valid_statuses:
                filters["status"] = ArtifactStatus(status_filter)
            # Unknown status values are ignored (treated as no filter).
        if recorded is not None:
            filters["recorded"] = recorded
        if has_transcript is not None:
            filters["has_transcript"] = has_transcript
        if search:
            filters["search"] = search

        artifacts = manager.list_with_filters(
            filters=filters,
            conversation_id=conv_id,
            server_id=srv_id,
            channel_id=chan_id,
            author_id=auth_id,
        )
        total = manager.count(filters)

        return ArtifactListResponse(
            items=[ArtifactResponse.model_validate(a) for a in artifacts],
            total=total,
            has_more=(offset + limit) < total,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list artifacts: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{artifact_id}",
    response_model=ArtifactResponse,
    summary="Get an artifact",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Artifact not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_artifact(
    artifact_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Fetch a single artifact by id."""
    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        manager = _get_manager()
        artifact = manager.get(aid)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.view",
        )

        return ArtifactResponse.model_validate(artifact)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/{artifact_id}",
    response_model=ArtifactResponse,
    summary="Update an artifact",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Artifact not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_artifact(
    artifact_id: str,
    body: ArtifactUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Update mutable fields of an artifact."""
    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        manager = _get_manager()
        artifact = manager.get(aid)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        # Author/owner can always edit their own artifact; otherwise require
        # the server permission.
        if (
            artifact.author_id != current_user.user_id
            and not _require_server_permission(
                current_user.user_id, artifact.server_id, "artifact.edit"
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Not authorized"}},
            )

        update_fields: Dict[str, Any] = {}
        if body.title is not None:
            update_fields["title"] = body.title
        if body.summary is not None:
            update_fields["summary"] = body.summary
        if body.status is not None:
            update_fields["status"] = ArtifactStatus(body.status.value)
        if body.payload is not None:
            update_fields["payload"] = body.payload
        if body.recorded is not None:
            update_fields["recorded"] = body.recorded
        if body.has_transcript is not None:
            update_fields["has_transcript"] = body.has_transcript
        if body.retention_policy is not None:
            update_fields["retention_policy"] = body.retention_policy

        updated = manager.update(aid, **update_fields)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )
        return ArtifactResponse.model_validate(updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/{artifact_id}",
    response_model=SuccessResponse,
    summary="Delete an artifact",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Artifact not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_artifact(
    artifact_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SuccessResponse:
    """Delete an artifact."""
    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        manager = _get_manager()
        artifact = manager.get(aid)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        if (
            artifact.author_id != current_user.user_id
            and not _require_server_permission(
                current_user.user_id, artifact.server_id, "artifact.delete"
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Not authorized"}},
            )

        if not manager.delete(aid):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Failed to delete"}},
            )
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/convert-upload",
    response_model=ArtifactResponse,
    summary="Convert an upload to an artifact",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Attachment not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def convert_upload(
    body: ConvertUploadRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Convert an existing attachment into an artifact."""
    try:
        conversation_id = (
            int(body.conversation_id) if body.conversation_id is not None else None
        )
        channel_id = int(body.channel_id) if body.channel_id is not None else None
        server_id = int(body.server_id) if body.server_id is not None else None

        _authorize_scope(
            current_user.user_id, conversation_id, server_id, "artifact.create"
        )

        db = api.get_db()
        if db is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail={"error": {"code": 500, "message": "Database unavailable"}},
            )

        attachment_id = int(body.attachment_id)
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

        max_size_mb = config.get("artifacts", {}).get("max_artifact_size_mb", 200)
        max_size_bytes = max_size_mb * 1024 * 1024
        if attachment.get("size", 0) > max_size_bytes:
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
        if not _is_conversation_member(
            messaging_mod, source_conv_id, current_user.user_id
        ):
            if not _require_server_permission(
                current_user.user_id, server_id, "artifact.create"
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"error": {"code": 403, "message": "Not authorized"}},
                )

        manager = _get_manager()
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

        return ArtifactResponse.model_validate(artifact)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to convert upload: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
