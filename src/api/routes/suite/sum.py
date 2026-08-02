"""Authenticated PlexiSum workbook routes."""

import src.api as api
from fastapi import APIRouter, Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.sum import (
    SumCellOperationRequest,
    SumOperationResponse,
    SumViewportResponse,
    SumWorkbookCreateRequest,
    SumWorkbookListResponse,
    SumWorkbookResponse,
)
from src.core.artifacts.capabilities import CapabilityState, get_capability
from src.core.suite import sum as sum_domain
from src.core.suite.sum.manager import (
    SumConflictError,
    SumManager,
    SumNotFoundError,
    SumPermissionError,
    SumValidationError,
)

router = APIRouter(prefix="/suite/sum", tags=["PlexiSum"])


def _require_sum_capability() -> None:
    """Fail closed when PlexiSum is disabled or unlicensed."""
    info = get_capability("plexisum")
    if info.state != CapabilityState.AVAILABLE:
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
        return SumManager(db, servers_module=api.get_servers())


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
    return HTTPException(status_code=500, detail="PlexiSum operation failed")


@router.get("/workbooks", response_model=SumWorkbookListResponse)
async def list_workbooks(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookListResponse:
    try:
        items, total = _manager().list_workbooks(current_user.user_id, limit, offset)
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
        )
        return SumWorkbookResponse.model_validate(workbook)
    except Exception as exc:
        raise _error(exc) from exc


@router.get("/workbooks/{workbook_id}", response_model=SumWorkbookResponse)
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


@router.delete("/workbooks/{workbook_id}", status_code=status.HTTP_204_NO_CONTENT)
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


@router.post("/workbooks/{workbook_id}/restore", response_model=SumWorkbookResponse)
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
)
async def read_viewport(
    workbook_id: str,
    sheet_id: str,
    row_start: int = Query(0, ge=0),
    row_end: int = Query(50, ge=1),
    column_start: int = Query(0, ge=0),
    column_end: int = Query(20, ge=1),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumViewportResponse:
    try:
        result = _manager().read_viewport(
            current_user.user_id,
            int(workbook_id),
            int(sheet_id),
            row_start,
            row_end,
            column_start,
            column_end,
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


__all__ = ["router"]
