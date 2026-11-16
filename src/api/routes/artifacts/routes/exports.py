"""Artifact export and op-log replay endpoints."""

from __future__ import annotations

from typing import Any, Dict

import utils.logger as logger
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.core.security.hardening import sanitize_header_filename

from ...artifacts_export import (
    _artifact_export_html,
    _artifact_export_markdown,
    _artifact_export_odt,
    _artifact_export_pdf,
    _artifact_export_text,
    _artifact_native_bytes,
)
from ...artifacts_helpers import (
    _AuthCache,
    _authorize_scope,
    _get_manager,
    _require_artifact_capability,
)
from ..constants import EXPORT_MIME_MAP
from ..validation import safe_download_filename, validate_export_format
from .common import parse_artifact_id

router = APIRouter(prefix="/artifacts", tags=["Artifacts"])


@router.get(
    "/{artifact_id}/export",
    response_class=Response,
    summary="Export an artifact",
)
async def _export_artifact_impl(
    artifact_id: str,
    export_format: str = Query("html", alias="export_format", max_length=32),
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
        aid = parse_artifact_id(artifact_id)

        export_format = validate_export_format(export_format)

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
            cache=_AuthCache(),
        )
        _require_artifact_capability(artifact.artifact_type)

        ascii_filename, utf8_filename = safe_download_filename(
            artifact.title, export_format
        )

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

        content_type = EXPORT_MIME_MAP.get(export_format, "application/octet-stream")
        disposition = (
            f'attachment; filename="{sanitize_header_filename(ascii_filename)}"; '
            f"filename*=UTF-8''{utf8_filename}"
        )

        def _chunk_iter() -> Any:
            yield data

        return StreamingResponse(
            _chunk_iter(),
            media_type=content_type,
            headers={
                "Content-Disposition": disposition,
                "X-Content-Type-Options": "nosniff",
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
    after_seq: int = Query(
        0, ge=0, le=2**63 - 1, description="Return only ops with seq > this"
    ),
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
        aid = parse_artifact_id(artifact_id)

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
            cache=_AuthCache(),
        )
        _require_artifact_capability(artifact.artifact_type)

        ops = manager.list_ops(aid, after_seq=after_seq, limit=limit)
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
