"""Presence tracking and frozen panes."""

from fastapi import Depends, HTTPException

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse, SnowflakeID
from src.api.schemas.sum import (
    SumFreezeRequest,
    SumFreezeResponse,
    SumPresenceResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


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
