"""Data validations and conditional formats."""

from fastapi import Depends, HTTPException, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumConditionalFormatListResponse,
    SumConditionalFormatRequest,
    SumConditionalFormatResponse,
    SumValidationListResponse,
    SumValidationRequest,
    SumValidationResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


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
