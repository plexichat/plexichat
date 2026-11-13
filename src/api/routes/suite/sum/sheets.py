"""Sheet creation, renaming, and deletion."""

from fastapi import Depends, HTTPException, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumSheetCreateRequest,
    SumSheetOpResponse,
    SumSheetRenameRequest,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


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
