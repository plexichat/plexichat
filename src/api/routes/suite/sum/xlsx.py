"""XLSX import and export."""

from typing import Optional

from fastapi import Depends, HTTPException, Query

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumCsvImportResponse,
    SumXlsxExportResponse,
    SumXlsxImportRequest,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


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
