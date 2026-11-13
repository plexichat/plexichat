"""Snapshot creation, listing, and restore."""

from fastapi import Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumSnapshotCreateRequest,
    SumSnapshotListResponse,
    SumSnapshotResponse,
    SumWorkbookResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


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
