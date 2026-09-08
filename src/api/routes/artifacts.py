"""
Artifact routes - REST API for the Artifacts feature.

Implements CRUD + listing for artifacts plus inline transcript emission.
Permission checks defer to the server RBAC layer (`artifact.view/create/
edit/delete/manage_retention`); for DM/group conversations (no server) the
caller must be a participant/owner of the conversation.

Shared helpers live in ``artifacts_helpers.py`` and export renderers in
``artifacts_export.py``; they are re-exported here so the historical import
path ``src.api.routes.artifacts`` keeps exposing every public name.
"""

import json
from typing import Any, Dict, List, Optional

import utils.logger as logger
import utils.config as config
import src.api as api
from fastapi import APIRouter, HTTPException, Depends, Query, Response, status
from fastapi.responses import StreamingResponse

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

# Re-exported helpers: stable import surface for tests and sibling modules
# (e.g. ``src.api.routes.suite.registry``). ``__all__`` marks them as used.
from src.api.routes.artifacts_helpers import (
    _artifact_response,
    _author_profile_cache,
    _authorize_scope,
    _conversation_server_id,
    _deep_merge_payload,
    _emit_artifact_message,
    _get_manager,
    _is_conversation_member,
    _artifact_capability_for_type,
    _my_permissions,
    _require_artifact_capability,
    _require_server_permission,
    _validate_artifact_scope,
    payload_revision,
    stale_payload_write,
)
from src.api.routes.artifacts_export import (
    _artifact_board_text,
    _artifact_export_html,
    _artifact_export_markdown,
    _artifact_export_odt,
    _artifact_export_pdf,
    _artifact_export_text,
    _artifact_native_bytes,
    _artifact_scribe_html,
    _artifact_script_text,
    _json_loads,
    _latin1_safe,
    _strip_html_markup,
    sanitize_rich_html,
)

__all__ = [
    "router",
    "sanitize_rich_html",
    "_artifact_board_text",
    "_artifact_capability_for_type",
    "_artifact_export_html",
    "_artifact_export_markdown",
    "_artifact_export_odt",
    "_artifact_export_pdf",
    "_artifact_export_text",
    "_artifact_native_bytes",
    "_artifact_response",
    "_artifact_scribe_html",
    "_artifact_script_text",
    "_author_profile_cache",
    "_authorize_scope",
    "_conversation_server_id",
    "_deep_merge_payload",
    "_emit_artifact_message",
    "_get_manager",
    "_is_conversation_member",
    "_json_loads",
    "_latin1_safe",
    "_my_permissions",
    "_require_artifact_capability",
    "_require_server_permission",
    "_strip_html_markup",
    "_validate_artifact_scope",
    "payload_revision",
    "stale_payload_write",
]

router = APIRouter(prefix="/artifacts", tags=["Artifacts"])

ARTIFACT_ICON = config.get("artifacts", {}).get("inline_icon", "📎")


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

        conversation_id, channel_id, server_id = _validate_artifact_scope(
            conversation_id, channel_id, server_id
        )
        _authorize_scope(
            current_user.user_id,
            conversation_id,
            server_id,
            "artifact.create",
            author_id=current_user.user_id,
        )

        manager = _get_manager()
        artifact_type = ArtifactType(body.artifact_type.value)
        required_feature = _require_artifact_capability(artifact_type)
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
            # Never trust a client-supplied license label as an authorization
            # decision; persist only the server-resolved capability.
            license_feature=(
                None if required_feature == "artifacts" else required_feature
            ),
        )

        author = {
            "id": str(current_user.user_id),
            "username": current_user.username,
        }
        _emit_artifact_message(ArtifactResponse.model_validate(artifact), author)

        return _artifact_response(current_user.user_id, artifact)
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
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    include_payload: bool = Query(True),
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactListResponse:
    """List artifacts with query filters.

    ``include_payload=False`` omits the (up to 1 MB) type-specific payload
    from every item so metadata-only listings (e.g. the Suite's Scribe/Script/
    Board libraries) never ship full document bodies over the wire. Single-artifact
    GETs and the chat artifacts pane keep ``include_payload=True``.
    """
    try:
        conv_id = int(conversation_id) if conversation_id is not None else None
        chan_id = int(channel_id) if channel_id is not None else None
        srv_id = int(server_id) if server_id is not None else None
        auth_id = int(author_id) if author_id is not None else None

        # Every list request is subject to the base artifacts capability,
        # including untyped listings.
        _require_artifact_capability(ArtifactType.UPLOAD)
        if conv_id is not None or chan_id is not None or srv_id is not None:
            conv_id, chan_id, srv_id = _validate_artifact_scope(
                conv_id, chan_id, srv_id
            )

        # An unscoped request is explicitly the caller's personal scope. Do
        # not authorize it as personal and then query all authors.
        scope_author_id = (
            auth_id
            if auth_id is not None
            else (current_user.user_id if conv_id is None and srv_id is None else None)
        )
        _authorize_scope(
            current_user.user_id,
            conv_id,
            srv_id,
            "artifact.view",
            author_id=scope_author_id,
        )
        if (
            auth_id is not None
            and conv_id is None
            and srv_id is None
            and auth_id != current_user.user_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"error": {"code": 403, "message": "Not authorized"}},
            )

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

        # Keep the count query identical to the item query's scope. Pagination
        # and ordering are intentionally left in ``filters`` because the
        # repository ignores them for COUNT while applying the scope keys.
        if conv_id is not None:
            filters["conversation_id"] = conv_id
        if srv_id is not None:
            filters["server_id"] = srv_id
        if chan_id is not None:
            filters["channel_id"] = chan_id
        if auth_id is not None:
            filters["author_id"] = auth_id
        elif conv_id is None and srv_id is None:
            filters["author_id"] = current_user.user_id

        artifacts = manager.list_with_filters(
            filters=filters,
            conversation_id=conv_id,
            server_id=srv_id,
            channel_id=chan_id,
            author_id=(
                auth_id
                if auth_id is not None
                else (
                    current_user.user_id if conv_id is None and srv_id is None else None
                )
            ),
        )
        total = manager.count(filters)

        author_cache = _author_profile_cache(artifacts)
        items = [
            _artifact_response(current_user.user_id, a, author_cache) for a in artifacts
        ]
        if not include_payload:
            for item in items:
                item.payload = {}

        return ArtifactListResponse(
            items=items,
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
        if source_conv_id is not None and not _is_conversation_member(
            messaging_mod, source_conv_id, current_user.user_id
        ):
            # Conversation non-members may still convert the upload if they hold
            # the server-level ``artifact.create`` permission (e.g. a server
            # admin acting on a channel they don't personally participate in).
            if not _require_server_permission(
                current_user.user_id, server_id, "artifact.create"
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
            conversation_id, channel_id, server_id
        )
        # Re-run authorization against the actual source scope after deriving
        # it from the database, rather than authorizing an omitted target scope.
        _authorize_scope(
            current_user.user_id,
            conversation_id,
            server_id,
            "artifact.create",
            author_id=current_user.user_id,
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

        return _artifact_response(current_user.user_id, artifact)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to convert upload: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/{artifact_id}/export",
    response_class=Response,
    summary="Export an artifact",
)
async def _export_artifact_impl(
    artifact_id: str,
    export_format: str = Query("html", alias="export_format"),
    current_user: TokenInfo = Depends(get_current_user),
) -> Response:
    """Export an artifact as a downloadable file in the requested format.

    Supported formats: html, pdf, md, odt, txt, plexiscribe (native),
    plexiscript, plexiboard.

    Returns the rendered bytes directly with the correct ``Content-Type`` and a
    ``Content-Disposition: attachment`` header so browsers offer a real file
    download (rather than a base64-encoded JSON envelope).
    """
    try:
        try:
            aid = int(artifact_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": {"code": 400, "message": "Invalid artifact ID"}},
            )

        valid_formats = {
            "html",
            "pdf",
            "md",
            "odt",
            "txt",
            "plexiscribe",
            "plexiscript",
            "plexiboard",
        }
        if export_format not in valid_formats:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": f"Unsupported format '{export_format}'. Supported: {', '.join(sorted(valid_formats))}",
                    }
                },
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
            author_id=artifact.author_id,
        )
        _require_artifact_capability(artifact.artifact_type)

        mime_map = {
            "html": "text/html",
            "pdf": "application/pdf",
            "md": "text/markdown",
            "odt": "application/vnd.oasis.opendocument.text",
            "txt": "text/plain",
            "plexiscribe": "application/vnd.plexichat.plexiscribe",
            "plexiscript": "application/vnd.plexichat.plexiscript",
            "plexiboard": "application/vnd.plexichat.plexiboard",
        }

        title_slug = (
            artifact.title.lower().replace(" ", "-")[:50]
            if artifact.title
            else "export"
        )
        # Ensure the slug is filesystem-safe (strip anything that isn't
        # alphanumeric, dash, or underscore so the Content-Disposition filename
        # is valid across platforms).
        safe_slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in title_slug)
        filename = f"{safe_slug}.{export_format}"

        payload = artifact.payload or {}
        artifact_type_value = artifact.artifact_type.value
        text = _artifact_export_text(artifact_type_value, payload, artifact.summary)

        if (
            export_format == "plexiscribe"
            and artifact.artifact_type.value == "plexiscribe"
        ):
            data = _artifact_native_bytes(payload.get("document"))
        elif (
            export_format == "plexiscript"
            and artifact.artifact_type.value == "plexiscript"
        ):
            data = _artifact_native_bytes(
                payload.get("source") or payload.get("content")
            )
        elif (
            export_format == "plexiboard"
            and artifact.artifact_type.value == "whiteboard"
        ):
            data = _artifact_native_bytes(payload.get("board"))
        elif export_format == "txt":
            data = text.encode("utf-8", "replace")
        elif export_format == "md":
            data = _artifact_export_markdown(artifact, text).encode("utf-8", "replace")
        elif export_format == "html":
            data = _artifact_export_html(artifact, artifact_type_value, payload).encode(
                "utf-8", "replace"
            )
        elif export_format == "pdf":
            data = _artifact_export_pdf(artifact, text)
        elif export_format == "odt":
            data = _artifact_export_odt(artifact, text)
        else:
            data = str(payload).encode("utf-8", "replace")

        content_type = mime_map.get(export_format, "application/octet-stream")
        # ASCII-safe fallback filename for the disposition header, plus a
        # UTF-8 ``filename*`` parameter so non-ASCII titles still resolve.
        ascii_filename = filename.encode("ascii", "ignore").decode("ascii") or filename

        def _chunk_iter() -> Any:
            yield data

        return StreamingResponse(
            _chunk_iter(),
            media_type=content_type,
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{ascii_filename}"; '
                    f"filename*=UTF-8''{filename}"
                ),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Export failed"}},
        )


@router.get(
    "/{artifact_id}/ops",
    response_model=Dict[str, Any],
    summary="List an artifact's persisted ops",
)
async def _list_artifact_ops_impl(
    artifact_id: str,
    after_seq: int = Query(0, description="Return only ops with seq > this"),
    limit: int = Query(500, ge=1, le=2000, description="Max ops to return"),
    current_user: TokenInfo = Depends(get_current_user),
) -> Dict[str, Any]:
    """Return the ordered ops log for an artifact.

    Lets clients reconnect to a collaborative artifact without a live WebSocket
    by replaying everything that happened after ``after_seq`` (default: all).
    Each entry is ``{seq, op_type, actor_id, created_at, op}``, the same wire
    shape carried inside ``ARTIFACT_SYNC``.
    """
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
            author_id=artifact.author_id,
        )
        _require_artifact_capability(artifact.artifact_type)

        ops = manager.list_ops(aid, after_seq=max(0, int(after_seq)), limit=int(limit))
        return {"artifact_id": str(aid), "ops": ops}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to list ops for artifact {artifact_id}: {e}", exc_info=True
        )
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
            author_id=artifact.author_id,
        )
        _require_artifact_capability(artifact.artifact_type)

        return _artifact_response(current_user.user_id, artifact)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch artifact {artifact_id}: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.patch(
    "/{artifact_id:int}",
    response_model=ArtifactResponse,
    summary="Update an artifact",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid input"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Not authorized"},
        404: {"model": ErrorResponse, "description": "Artifact not found"},
        409: {
            "model": ErrorResponse,
            "description": "Stale revision: the artifact changed since this write was based on",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_artifact(
    artifact_id: int,
    body: ArtifactUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> ArtifactResponse:
    """Update mutable fields of an artifact."""
    try:
        manager = _get_manager()
        artifact = manager.get(artifact_id)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        _require_artifact_capability(artifact.artifact_type)

        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.edit",
            author_id=artifact.author_id,
        )

        update_fields: Dict[str, Any] = {}
        if body.title is not None:
            update_fields["title"] = body.title
        if body.summary is not None:
            update_fields["summary"] = body.summary
        if body.status is not None:
            update_fields["status"] = ArtifactStatus(body.status.value)
        if body.payload is not None:
            # Rev guard: the code/text editor stamps a monotonic ``rev`` into
            # the payload on every autosave. A write whose rev is not NEWER
            # than the persisted rev is stale — a concurrent tab already
            # committed a later revision, and letting the older payload
            # through would silently overwrite it (lost update). Reject with
            # 409 so the client re-fetches and re-bases instead of retrying.
            # Only enforced when both payloads carry a numeric ``rev``; other
            # artifact types and legacy clients are unaffected.
            if stale_payload_write(artifact.payload, body.payload):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": {
                            "code": 409,
                            "message": (
                                "Stale artifact revision: the artifact is already "
                                f"at revision {payload_revision(artifact.payload)}, "
                                f"but this write is for revision "
                                f"{payload_revision(body.payload)}. Reload the "
                                "artifact and retry."
                            ),
                        }
                    },
                )
            # Merge (not replace) so a partial payload no longer clobbers
            # concurrently-updated keys. Editors that send the full payload
            # dict are unaffected (a superset merge is a no-op replacement).
            existing_payload = (
                artifact.payload if isinstance(artifact.payload, dict) else {}
            )
            update_fields["payload"] = _deep_merge_payload(
                existing_payload, body.payload
            )
        if "payload" in update_fields:
            try:
                merged_size = len(
                    json.dumps(update_fields["payload"], ensure_ascii=False).encode(
                        "utf-8"
                    )
                )
            except (TypeError, ValueError):
                merged_size = -1
            if merged_size > 1024 * 1024:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": {
                            "code": 400,
                            "message": "payload exceeds maximum size of 1 MB",
                        }
                    },
                )
        if body.recorded is not None:
            update_fields["recorded"] = body.recorded
        if body.has_transcript is not None:
            update_fields["has_transcript"] = body.has_transcript
        # ``model_fields_set`` distinguishes an omitted optional field from an
        # explicit null, allowing an operator/editor to clear a policy and its
        # computed expiry.
        if "retention_policy" in getattr(body, "model_fields_set", set()):
            update_fields["retention_policy"] = body.retention_policy

        updated = manager.update(artifact_id, **update_fields)
        if updated is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )
        return _artifact_response(current_user.user_id, updated)
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

        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.delete",
            author_id=artifact.author_id,
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
