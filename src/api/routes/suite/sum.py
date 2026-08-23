"""Authenticated PlexiSum workbook routes."""

from typing import Optional

import src.api as api
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse, SnowflakeID
from src.api.schemas.sum import (
    SumCellOperationRequest,
    SumCommentListResponse,
    SumCommentRequest,
    SumCommentResponse,
    SumConditionalFormatListResponse,
    SumConditionalFormatRequest,
    SumConditionalFormatResponse,
    SumCreateFromTemplateRequest,
    SumCsvExportResponse,
    SumCsvImportRequest,
    SumCsvImportResponse,
    SumFeatureCreateRequest,
    SumFeatureListResponse,
    SumFeatureResponse,
    SumFeatureUpdateRequest,
    SumFreezeRequest,
    SumFreezeResponse,
    SumGrantListResponse,
    SumPinRequest,
    SumWorkbookFileResponse,
    SumWorkbookUploadRequest,
    SumWorkbookUploadResponse,
    SumGrantRequest,
    SumGrantResponse,
    SumOperationResponse,
    SumPresenceResponse,
    SumRangePermissionListResponse,
    SumRangePermissionRequest,
    SumRangePermissionResponse,
    SumReplayOperation,
    SumReplayResponse,
    SumSheetCreateRequest,
    SumSheetOpResponse,
    SumSheetRenameRequest,
    SumSortRequest,
    SumSortResponse,
    SumSnapshotCreateRequest,
    SumSnapshotListResponse,
    SumSnapshotResponse,
    SumTemplateRequest,
    SumValidationListResponse,
    SumValidationRequest,
    SumValidationResponse,
    SumViewportResponse,
    SumWorkbookCreateRequest,
    SumWorkbookListResponse,
    SumWorkbookResponse,
    SumXlsxExportResponse,
    SumXlsxImportRequest,
)
from src.core.artifacts.capabilities import get_capability, is_usable
from src.core.suite import sum as sum_domain
from src.core.suite.sum.manager import (
    SumConflictError,
    SumManager,
    SumNotFoundError,
    SumPermissionError,
    SumValidationError,
)

router = APIRouter(
    prefix="/suite/sum",
    tags=["PlexiSum"],
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "Access denied"},
    },
)


def _require_sum_capability() -> None:
    """Fail closed when PlexiSum is disabled and not in evaluation mode."""
    info = get_capability("plexisum")
    if not is_usable(info.state):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": 403,
                    "message": "PlexiSum is unavailable",
                    "state": info.state.value,
                }
            },
        )


def _manager() -> SumManager:
    _require_sum_capability()
    try:
        return sum_domain.get_manager()
    except RuntimeError:
        # Keep isolated route tests and embedded callers functional when they
        # provide a database without going through the full bootstrap path.
        db = api.get_db()
        if db is None:
            raise HTTPException(status_code=503, detail="Database unavailable")
        # Keep the fixture/embedded fallback on the same compatibility path as
        # normal startup; constructing SumManager directly is not enough for
        # already-applied databases missing additive metadata tables/columns.
        return sum_domain.setup(
            db,
            servers_module=api.get_servers(),
            media=api.get_media(),
        )


def _error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, SumNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, SumPermissionError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, SumConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, SumValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    # Unknown failures must never vanish silently: log the full traceback so
    # a bare 500 is always diagnosable from latest.log.
    import utils.logger as logger

    logger.error(f"PlexiSum operation failed: {exc}", exc_info=True)
    return HTTPException(status_code=500, detail="PlexiSum operation failed")


@router.get(
    "/workbooks",
    response_model=SumWorkbookListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_workbooks(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    template: bool = Query(
        False, description="List template workbooks instead of regular workbooks"
    ),
    search: str = Query("", max_length=200, description="Title substring search"),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookListResponse:
    try:
        items, total = _manager().list_workbooks(
            current_user.user_id,
            limit,
            offset,
            template=template,
            search=search.strip() or None,
        )
        return SumWorkbookListResponse(
            items=[SumWorkbookResponse.model_validate(item) for item in items],
            total=total,
            has_more=offset + len(items) < total,
        )
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks",
    response_model=SumWorkbookResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_workbook(
    body: SumWorkbookCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        workbook = _manager().create_workbook(
            current_user.user_id,
            body.title,
            body.scope,
            int(body.server_id) if body.server_id is not None else None,
            template=body.template,
        )
        return SumWorkbookResponse.model_validate(workbook)
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/from-template",
    response_model=SumWorkbookResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_from_template(
    body: SumCreateFromTemplateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        workbook = _manager().create_from_template(
            current_user.user_id,
            int(body.template_id),
            body.title,
            body.scope,
            int(body.server_id) if body.server_id is not None else None,
        )
        return SumWorkbookResponse.model_validate(workbook)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid template id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/workbooks/{workbook_id}/pin",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_pinned(
    workbook_id: str,
    body: SumPinRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        workbook = _manager().set_pinned(
            current_user.user_id, int(workbook_id), body.pinned
        )
        return SumWorkbookResponse.model_validate(workbook)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/workbooks/{workbook_id}/template",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_template(
    workbook_id: str,
    body: SumTemplateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        workbook = _manager().set_template(
            current_user.user_id, int(workbook_id), body.is_template
        )
        return SumWorkbookResponse.model_validate(workbook)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_workbook(
    workbook_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        return SumWorkbookResponse.model_validate(
            _manager().get_workbook(current_user.user_id, int(workbook_id))
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/workbooks/{workbook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_workbook(
    workbook_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().delete_workbook(current_user.user_id, int(workbook_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/restore",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def restore_workbook(
    workbook_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        return SumWorkbookResponse.model_validate(
            _manager().restore_workbook(current_user.user_id, int(workbook_id))
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/viewport",
    response_model=SumViewportResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def read_viewport(
    workbook_id: str,
    sheet_id: str,
    row_start: int = Query(0, ge=0),
    row_end: int = Query(50, ge=1),
    column_start: int = Query(0, ge=0),
    column_end: int = Query(20, ge=1),
    filters: str = Query("", description="JSON-encoded list of filter criteria"),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumViewportResponse:
    try:
        import json as json_module

        parsed_filters = None
        if filters and filters.strip():
            try:
                parsed_filters = json_module.loads(filters)
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid filters JSON")
            if not isinstance(parsed_filters, list):
                raise HTTPException(status_code=400, detail="filters must be a list")
        result = _manager().read_viewport(
            current_user.user_id,
            int(workbook_id),
            int(sheet_id),
            row_start,
            row_end,
            column_start,
            column_end,
            parsed_filters,
        )
        return SumViewportResponse.model_validate(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/operations",
    response_model=SumOperationResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def apply_operation(
    workbook_id: str,
    sheet_id: str,
    body: SumCellOperationRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumOperationResponse:
    try:
        result = _manager().apply_operation(
            current_user.user_id,
            int(workbook_id),
            int(sheet_id),
            body.operation_id,
            body.operation_type,
            body.base_revision,
            body.cells,
        )
        return SumOperationResponse.model_validate(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/sort",
    response_model=SumSortResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def sort_range(
    workbook_id: str,
    sheet_id: str,
    body: SumSortRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumSortResponse:
    """Stable-sort a row range by a column's computed value."""
    try:
        result = _manager().sort_range(
            current_user.user_id,
            int(workbook_id),
            int(sheet_id),
            body.column,
            body.start_row,
            body.end_row,
            body.direction,
            body.base_revision,
        )
        return SumSortResponse.model_validate(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/operations",
    response_model=SumReplayResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def replay_operations(
    workbook_id: str,
    after_revision: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=1000),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumReplayResponse:
    try:
        operations = _manager().replay_operations(
            current_user.user_id, int(workbook_id), after_revision, limit
        )
        return SumReplayResponse(
            workbook_id=SnowflakeID(workbook_id),
            after_revision=after_revision,
            operations=[SumReplayOperation.model_validate(op) for op in operations],
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/snapshots",
    response_model=SumSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_snapshot(
    workbook_id: str,
    body: SumSnapshotCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumSnapshotResponse:
    try:
        return SumSnapshotResponse.model_validate(
            _manager().create_snapshot(current_user.user_id, int(workbook_id))
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/snapshots",
    response_model=SumSnapshotListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_snapshots(
    workbook_id: str,
    limit: int = Query(50, ge=1, le=100),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumSnapshotListResponse:
    try:
        items = _manager().list_snapshots(current_user.user_id, int(workbook_id), limit)
        return SumSnapshotListResponse(
            items=[SumSnapshotResponse.model_validate(item) for item in items]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/snapshots/{snapshot_id}/restore",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def restore_snapshot(
    workbook_id: str,
    snapshot_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        return SumWorkbookResponse.model_validate(
            _manager().restore_snapshot(
                current_user.user_id, int(workbook_id), int(snapshot_id)
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or snapshot id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets",
    response_model=SumSheetOpResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_sheet(
    workbook_id: str,
    body: SumSheetCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumSheetOpResponse:
    try:
        return SumSheetOpResponse.model_validate(
            _manager().create_sheet(
                current_user.user_id,
                int(workbook_id),
                body.name,
                body.position,
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/rename",
    response_model=SumSheetOpResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def rename_sheet(
    workbook_id: str,
    sheet_id: str,
    body: SumSheetRenameRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumSheetOpResponse:
    try:
        return SumSheetOpResponse.model_validate(
            _manager().rename_sheet(
                current_user.user_id, int(workbook_id), int(sheet_id), body.name
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/workbooks/{workbook_id}/sheets/{sheet_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_sheet(
    workbook_id: str,
    sheet_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().delete_sheet(current_user.user_id, int(workbook_id), int(sheet_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/file",
    response_model=SumWorkbookFileResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def download_file(
    workbook_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookFileResponse:
    """Download the current ``.plexisum`` workbook file (authorization rechecked)."""
    try:
        result = _manager().download_file(current_user.user_id, int(workbook_id))
        return SumWorkbookFileResponse.model_validate(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/file",
    response_model=SumWorkbookUploadResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_file(
    workbook_id: str,
    body: SumWorkbookUploadRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookUploadResponse:
    """Replace the workbook file with an uploaded ``.plexisum`` file."""
    try:
        import base64 as base64_module

        raw = body.content
        if raw.startswith("data:"):
            # Accept only a base64 data URL. Do not silently discard invalid
            # characters: permissive decoding can turn malformed input into a
            # different valid SQLite file.
            comma = raw.find(",")
            if comma == -1 or ";base64" not in raw[:comma].lower():
                raise HTTPException(status_code=400, detail="Invalid file payload")
            encoded = raw[comma + 1 :]
        else:
            encoded = raw
        if len(encoded) > 70_000_000:
            raise HTTPException(status_code=413, detail="Workbook file is too large")
        try:
            raw = base64_module.b64decode(encoded, validate=True)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="Invalid base64 payload"
            ) from exc
        if len(raw) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Workbook file is too large")
        result = _manager().upload_file(
            current_user.user_id,
            int(workbook_id),
            raw,
            body.base_revision,
        )
        return SumWorkbookUploadResponse.model_validate(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/csv",
    response_model=SumCsvExportResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def export_csv(
    workbook_id: str,
    sheet_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumCsvExportResponse:
    try:
        return SumCsvExportResponse.model_validate(
            _manager().export_csv(current_user.user_id, int(workbook_id), int(sheet_id))
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/csv",
    response_model=SumCsvImportResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def import_csv(
    workbook_id: str,
    sheet_id: str,
    body: SumCsvImportRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumCsvImportResponse:
    try:
        return SumCsvImportResponse.model_validate(
            _manager().import_csv(
                current_user.user_id,
                int(workbook_id),
                int(sheet_id),
                body.content,
                body.base_revision,
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/grants",
    response_model=SumGrantListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_grants(
    workbook_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumGrantListResponse:
    try:
        items = _manager().list_grants(current_user.user_id, int(workbook_id))
        return SumGrantListResponse(
            items=[SumGrantResponse.model_validate(item) for item in items]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/grants",
    response_model=SumGrantResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def grant_workbook(
    workbook_id: str,
    body: SumGrantRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumGrantResponse:
    try:
        return SumGrantResponse.model_validate(
            _manager().grant_workbook(
                current_user.user_id,
                int(workbook_id),
                int(body.subject_id),
                body.permissions,
                body.expires_at,
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or subject id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def revoke_grant(
    grant_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().revoke_grant(current_user.user_id, int(grant_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid grant id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/range-permissions",
    response_model=SumRangePermissionListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_range_permissions(
    workbook_id: str,
    sheet_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumRangePermissionListResponse:
    try:
        items = _manager().list_range_permissions(
            current_user.user_id, int(workbook_id), int(sheet_id)
        )
        return SumRangePermissionListResponse(
            items=[SumRangePermissionResponse.model_validate(item) for item in items]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/range-permissions",
    response_model=SumRangePermissionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_range_permission(
    workbook_id: str,
    sheet_id: str,
    body: SumRangePermissionRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumRangePermissionResponse:
    try:
        return SumRangePermissionResponse.model_validate(
            _manager().set_range_permission(
                current_user.user_id,
                int(workbook_id),
                int(sheet_id),
                body.row_start,
                body.row_end,
                body.column_start,
                body.column_end,
                body.permission,
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/workbooks/{workbook_id}/range-permissions/{range_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def remove_range_permission(
    workbook_id: str,
    range_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().remove_range_permission(
            current_user.user_id, int(workbook_id), int(range_id)
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or range id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


# ── realtime collaboration / presence ───────────────────────────────────────


@router.get(
    "/workbooks/{workbook_id}/presence",
    response_model=SumPresenceResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_presence(
    workbook_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumPresenceResponse:
    try:
        users = _manager().list_presence(current_user.user_id, int(workbook_id))
        return SumPresenceResponse(workbook_id=SnowflakeID(workbook_id), users=users)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.put(
    "/workbooks/{workbook_id}/presence",
    response_model=SumPresenceResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_presence(
    workbook_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumPresenceResponse:
    """Heartbeat presence; returns the current viewer list."""
    try:
        users = _manager().set_presence(
            current_user.user_id, int(workbook_id), current_user.username
        )
        return SumPresenceResponse(workbook_id=SnowflakeID(workbook_id), users=users)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


# ── freeze panes ─────────────────────────────────────────────────────────────


@router.get(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/freeze",
    response_model=SumFreezeResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_freeze(
    workbook_id: str,
    sheet_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumFreezeResponse:
    try:
        return SumFreezeResponse.model_validate(
            _manager().get_freeze(current_user.user_id, int(workbook_id), int(sheet_id))
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.put(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/freeze",
    response_model=SumFreezeResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_freeze(
    workbook_id: str,
    sheet_id: str,
    body: SumFreezeRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumFreezeResponse:
    try:
        return SumFreezeResponse.model_validate(
            _manager().set_freeze(
                current_user.user_id,
                int(workbook_id),
                int(sheet_id),
                body.freeze_rows,
                body.freeze_columns,
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


# ── data validation ──────────────────────────────────────────────────────────


@router.get(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/validations",
    response_model=SumValidationListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_validations(
    workbook_id: str,
    sheet_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumValidationListResponse:
    try:
        items = _manager().list_validations(
            current_user.user_id, int(workbook_id), int(sheet_id)
        )
        return SumValidationListResponse(
            items=[SumValidationResponse.model_validate(item) for item in items]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/validations",
    response_model=SumValidationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_validation(
    workbook_id: str,
    sheet_id: str,
    body: SumValidationRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumValidationResponse:
    try:
        return SumValidationResponse.model_validate(
            _manager().set_validation(
                current_user.user_id,
                int(workbook_id),
                int(sheet_id),
                body.rule_type,
                body.row_start,
                body.row_end,
                body.column_start,
                body.column_end,
                dict(body.criteria),
                body.allow_blank,
                body.message,
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/validations/{validation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_validation(
    workbook_id: str,
    sheet_id: str,
    validation_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().delete_validation(
            current_user.user_id,
            int(workbook_id),
            validation_id,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or validation id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


# ── conditional formatting ───────────────────────────────────────────────────


@router.get(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/conditional-formats",
    response_model=SumConditionalFormatListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_conditional_formats(
    workbook_id: str,
    sheet_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumConditionalFormatListResponse:
    try:
        items = _manager().list_conditional_formats(
            current_user.user_id, int(workbook_id), int(sheet_id)
        )
        return SumConditionalFormatListResponse(
            items=[SumConditionalFormatResponse.model_validate(item) for item in items]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/conditional-formats",
    response_model=SumConditionalFormatResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_conditional_format(
    workbook_id: str,
    sheet_id: str,
    body: SumConditionalFormatRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumConditionalFormatResponse:
    try:
        return SumConditionalFormatResponse.model_validate(
            _manager().set_conditional_format(
                current_user.user_id,
                int(workbook_id),
                int(sheet_id),
                body.rule_type,
                body.row_start,
                body.row_end,
                body.column_start,
                body.column_end,
                dict(body.criteria),
                dict(body.style),
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/conditional-formats/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_conditional_format(
    workbook_id: str,
    sheet_id: str,
    rule_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().delete_conditional_format(
            current_user.user_id, int(workbook_id), rule_id
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or rule id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


# ── cell comments ────────────────────────────────────────────────────────────


@router.get(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/comments",
    response_model=SumCommentListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_comments(
    workbook_id: str,
    sheet_id: str,
    row: Optional[int] = Query(None, ge=0),
    column: Optional[int] = Query(None, ge=0),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumCommentListResponse:
    try:
        items = _manager().list_comments(
            current_user.user_id,
            int(workbook_id),
            int(sheet_id),
            row,
            column,
        )
        return SumCommentListResponse(
            items=[SumCommentResponse.model_validate(item) for item in items]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/comments",
    response_model=SumCommentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def add_comment(
    workbook_id: str,
    sheet_id: str,
    body: SumCommentRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumCommentResponse:
    try:
        return SumCommentResponse.model_validate(
            _manager().add_comment(
                current_user.user_id,
                int(workbook_id),
                int(sheet_id),
                body.row,
                body.column,
                body.body,
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/workbooks/{workbook_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def resolve_comment(
    workbook_id: str,
    comment_id: str,
    resolved: bool = Query(True),
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().resolve_comment(
            current_user.user_id, int(workbook_id), comment_id, resolved
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or comment id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/workbooks/{workbook_id}/comments/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_comment(
    workbook_id: str,
    comment_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().delete_comment(current_user.user_id, int(workbook_id), comment_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or comment id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


# ── XLSX import / export ─────────────────────────────────────────────────────


@router.get(
    "/workbooks/{workbook_id}/xlsx",
    response_model=SumXlsxExportResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def export_xlsx(
    workbook_id: str,
    sheet_id: Optional[str] = Query(None),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumXlsxExportResponse:
    try:
        sheet = int(sheet_id) if sheet_id else None
        return SumXlsxExportResponse.model_validate(
            _manager().export_xlsx(current_user.user_id, int(workbook_id), sheet)
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/xlsx",
    response_model=SumCsvImportResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def import_xlsx(
    workbook_id: str,
    sheet_id: str,
    body: SumXlsxImportRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumCsvImportResponse:
    try:
        return SumCsvImportResponse.model_validate(
            _manager().import_xlsx(
                current_user.user_id,
                int(workbook_id),
                int(sheet_id),
                body.content,
                body.base_revision,
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/features",
    response_model=SumFeatureListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_features(
    workbook_id: str,
    feature_type: Optional[str] = Query(None, pattern="^(chart|pivot|sparkline)$"),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumFeatureListResponse:
    try:
        features = _manager().list_features(
            current_user.user_id, int(workbook_id), feature_type
        )
        return SumFeatureListResponse(
            items=[SumFeatureResponse.model_validate(f) for f in features]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/features",
    response_model=SumFeatureResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_feature(
    workbook_id: str,
    body: SumFeatureCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumFeatureResponse:
    try:
        feature = _manager().create_feature(
            current_user.user_id,
            int(workbook_id),
            body.feature_type,
            body.definition,
            body.sheet_id,
        )
        return SumFeatureResponse.model_validate(feature)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/workbooks/{workbook_id}/features/{feature_id}",
    response_model=SumFeatureResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_feature(
    workbook_id: str,
    feature_id: str,
    body: SumFeatureUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumFeatureResponse:
    try:
        feature = _manager().update_feature(
            current_user.user_id,
            int(workbook_id),
            int(feature_id),
            definition=body.definition,
            sheet_id=body.sheet_id,
        )
        return SumFeatureResponse.model_validate(feature)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or feature id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/workbooks/{workbook_id}/features/{feature_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_feature(
    workbook_id: str,
    feature_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().delete_feature(
            current_user.user_id, int(workbook_id), int(feature_id)
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or feature id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


__all__ = ["router"]
