"""Artifact create, list, fetch, update and delete endpoints."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import utils.logger as logger
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.artifacts import (
    ArtifactCreateRequest,
    ArtifactListResponse,
    ArtifactResponse,
    ArtifactUpdateRequest,
)
from src.api.schemas.common import (
    ErrorResponse,
    SnowflakeIDParam,
    SuccessResponse,
)
from src.core.artifacts.models import ArtifactStatus, ArtifactType

from ...artifacts_helpers import (
    _AuthCache,
    _artifact_type_enabled,
    _artifact_response,
    _author_profile_cache,
    _authorize_scope,
    _deep_merge_payload,
    _emit_artifact_message,
    _get_manager,
    _prime_conversation_server_cache,
    _require_artifact_capability,
    _validate_artifact_scope,
    payload_revision,
    stale_payload_write,
)
from .common import _parse_optional_id, parse_artifact_id

router = APIRouter(prefix="/artifacts", tags=["Artifacts"])


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

        auth_cache = _AuthCache()
        conversation_id, channel_id, server_id = _validate_artifact_scope(
            conversation_id, channel_id, server_id, auth_cache
        )
        _authorize_scope(
            current_user.user_id,
            conversation_id,
            server_id,
            "artifact.create",
            author_id=current_user.user_id,
            cache=auth_cache,
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

        return _artifact_response(current_user.user_id, artifact, auth_cache=auth_cache)
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
    search: Optional[str] = Query(None, max_length=256),
    sort_by: str = Query("created_at", max_length=32),
    sort_order: str = Query("desc", max_length=8),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1000000),
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
        conv_id = _parse_optional_id(conversation_id, "conversation ID")
        chan_id = _parse_optional_id(channel_id, "channel ID")
        srv_id = _parse_optional_id(server_id, "server ID")
        auth_id = _parse_optional_id(author_id, "author ID")

        # Every list request is subject to the base artifacts capability,
        # including untyped listings.
        _require_artifact_capability(ArtifactType.UPLOAD)
        auth_cache = _AuthCache()
        if conv_id is not None or chan_id is not None or srv_id is not None:
            conv_id, chan_id, srv_id = _validate_artifact_scope(
                conv_id, chan_id, srv_id, auth_cache
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
            cache=auth_cache,
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

            # The base `artifacts` capability checked above does not cover the
            # per-type gates (whiteboard / scribe / script). Without this, a
            # `?artifact_type=whiteboard` filter listed those artifacts even
            # when their license/config gate was disabled, which the single
            # artifact GET correctly refuses. Same gate, no bypass.
            for artifact_type_value in types:
                _require_artifact_capability(artifact_type_value)

        filters: Dict[str, Any] = {
            "sort_by": sort_by,
            "sort_order": sort_order,
            "limit": limit,
            "offset": offset,
        }
        if types is not None:
            # An explicit filter already 403'd above for any disabled type.
            filters["artifact_type"] = types
        else:
            # Fail closed: an untyped listing must apply the same per-type
            # capability gate that the single-artifact GET and explicit type
            # filters enforce. Encoding it as a SQL filter keeps the page and
            # the COUNT query consistent (so pagination stays correct) instead
            # of fetching disabled-type rows and dropping them after the fact.
            filters["artifact_type"] = [
                t for t in ArtifactType if _artifact_type_enabled(t)
            ]
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

        # Resolve every item's conversation ``metadata.server_id`` once, instead
        # of letting _artifact_response -> _my_permissions re-read the same
        # msg_conversations row once per artifact (N+1 across a page).
        _prime_conversation_server_cache(artifacts, auth_cache)
        author_cache = _author_profile_cache(artifacts)
        items = [
            _artifact_response(current_user.user_id, a, author_cache, auth_cache)
            for a in artifacts
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
        aid = parse_artifact_id(artifact_id)

        manager = _get_manager()
        artifact = manager.get(aid)
        if artifact is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Artifact not found"}},
            )

        auth_cache = _AuthCache()
        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.view",
            author_id=artifact.author_id,
            cache=auth_cache,
        )
        _require_artifact_capability(artifact.artifact_type)

        return _artifact_response(current_user.user_id, artifact, auth_cache=auth_cache)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch artifact {artifact_id}: {e}", exc_info=True)
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
        409: {
            "model": ErrorResponse,
            "description": "Stale revision: the artifact changed since this write was based on",
        },
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_artifact(
    artifact_id: SnowflakeIDParam,
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

        auth_cache = _AuthCache()
        _authorize_scope(
            current_user.user_id,
            artifact.conversation_id,
            artifact.server_id,
            "artifact.edit",
            author_id=artifact.author_id,
            cache=auth_cache,
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
            #
            # New-style writes carry the expected rev at the TOP level
            # (``body.rev``) and are guarded atomically below via
            # ``UPDATE ... WHERE rev = ?``; this legacy payload-carried guard
            # only applies when the caller did not supply a top-level rev.
            if body.rev is None and stale_payload_write(artifact.payload, body.payload):
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
                # SECURITY: measure the exact representation the repository
                # persists (``_json_dumps`` -> default ``json.dumps``,
                # ensure_ascii=True). A compact ``ensure_ascii=False`` form
                # undercounts non-ASCII payloads, which are escaped to
                # ``\uXXXX`` (up to ~3x) once stored, defeating the 1 MB cap.
                merged_size = len(
                    json.dumps(update_fields["payload"]).encode("utf-8")
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

        if body.rev is not None:
            # Optimistic concurrency on the dedicated rev column: the row is
            # updated atomically only while its revision still matches, and the
            # revision advances by one in the same statement — no read-modify-
            # write race. A zero-row update means a concurrent writer already
            # moved the revision: reject the stale write with 409.
            updated = manager.update(
                artifact_id, expected_rev=body.rev, **update_fields
            )
            if updated is None:
                current = manager.get(artifact_id)
                if current is None:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail={
                            "error": {"code": 404, "message": "Artifact not found"}
                        },
                    )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": {
                            "code": 409,
                            "message": (
                                "Stale artifact revision: the artifact is already "
                                f"at revision {current.rev}, but this write expects "
                                f"revision {body.rev}. Reload the artifact and retry."
                            ),
                        }
                    },
                )
        else:
            updated = manager.update(artifact_id, **update_fields)
            if updated is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail={"error": {"code": 404, "message": "Artifact not found"}},
                )
        return _artifact_response(current_user.user_id, updated, auth_cache=auth_cache)
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
            "artifact.delete",
            author_id=artifact.author_id,
            cache=_AuthCache(),
        )

        if not manager.delete(aid, purging_user_id=current_user.user_id):
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
