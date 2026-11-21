"""Viewport reads, mutations, sorting, and replay."""

from fastapi import Depends, HTTPException, Query

from src.core.security.hardening import parse_id

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

#: ``filters`` is a caller-supplied, JSON-encoded list of criteria carried in
#: the query string (not the request body, which the API's body cap covers).
#: Without a ceiling a caller can make us allocate a multi-megabyte string and
#: ``json.loads`` it into a huge Python object before the manager's
#: ``filters[:50]`` slice ever bounds the work.
#: 50 small criteria are all the manager consumes, so a few KiB is generous.
MAX_VIEWPORT_FILTERS_JSON_LENGTH = 8 * 1024


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
    filters: str = Query(
        "",
        max_length=MAX_VIEWPORT_FILTERS_JSON_LENGTH,
        description="JSON-encoded list of filter criteria",
    ),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumViewportResponse:
    # Defence in depth: FastAPI enforces ``max_length`` on the request path,
    # but direct callers (and any future framework change that skips that
    # validation) must not be able to hand an unbounded blob to ``json.loads``.
    if filters and len(filters) > MAX_VIEWPORT_FILTERS_JSON_LENGTH:
        raise HTTPException(status_code=400, detail="filters JSON is too large")
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
        # Every other PlexiSum route module parses string IDs with the shared
        # ASCII-only, snowflake-bounded gate. Bare ``int()`` here is a parser
        # differential: it silently accepts Unicode digit homoglyphs
        # (``"٤٢"``/``"４２"`` -> 42), PEP 515 separators (``"1_0"`` -> 10),
        # signs and 128-bit values, letting a caller address another user's
        # workbook/sheet through an ID the rest of the API rejects.
        result = _manager().read_viewport(
            current_user.user_id,
            parse_id(workbook_id, "workbook_id"),
            parse_id(sheet_id, "sheet_id"),
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
            parse_id(workbook_id, "workbook_id"),
            parse_id(sheet_id, "sheet_id"),
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
            parse_id(workbook_id, "workbook_id"),
            parse_id(sheet_id, "sheet_id"),
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
        workbook_pk = parse_id(workbook_id, "workbook_id")
        operations = _manager().replay_operations(
            current_user.user_id, workbook_pk, after_revision, limit
        )
        return SumReplayResponse(
            workbook_id=SnowflakeID(workbook_pk),
            after_revision=after_revision,
            operations=[SumReplayOperation.model_validate(op) for op in operations],
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc
