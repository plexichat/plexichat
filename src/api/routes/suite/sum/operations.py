"""Viewport reads, mutations, sorting, and replay."""

from fastapi import Depends, HTTPException, Query

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse, SnowflakeID
from src.api.schemas.sum import (
    SumCellOperationRequest,
    SumOperationResponse,
    SumReplayOperation,
    SumReplayResponse,
    SumSortRequest,
    SumSortResponse,
    SumViewportResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


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
