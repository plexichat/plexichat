"""Workbook and range grant management."""

from fastapi import Depends, HTTPException, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumGrantListResponse,
    SumGrantRequest,
    SumGrantResponse,
    SumRangePermissionListResponse,
    SumRangePermissionRequest,
    SumRangePermissionResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


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
