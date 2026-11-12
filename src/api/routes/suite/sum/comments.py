"""Cell comment threads."""

from typing import Optional

from fastapi import Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumCommentListResponse,
    SumCommentRequest,
    SumCommentResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


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
