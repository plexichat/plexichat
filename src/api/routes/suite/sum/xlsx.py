"""XLSX import and export."""

from src.core.security.hardening import parse_id
from typing import Optional

from fastapi import Depends, HTTPException, Query

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.routes.users.helpers import require_user_token
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
    sheet_id: Optional[str] = Query(None, max_length=32),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumXlsxExportResponse:
    # See files.download_file: the manager authorizes on ``user_id``, which for
    # a bot credential is the owner. Reject bot credentials up front.
    require_user_token(current_user)
    try:
        sheet = parse_id(sheet_id, "sheet_id") if sheet_id else None
        return SumXlsxExportResponse.model_validate(
            _manager().export_xlsx(
                current_user.user_id, parse_id(workbook_id, "workbook_id"), sheet
            )
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
    # See export_xlsx: the manager authorizes this WRITE on ``user_id``, which
    # for a bot credential is the owner. Reject bot credentials before they can
    # mutate cells in the owner's private workbook.
    require_user_token(current_user)
    try:
        return SumCsvImportResponse.model_validate(
            _manager().import_xlsx(
                current_user.user_id,
                parse_id(workbook_id, "workbook_id"),
                parse_id(sheet_id, "sheet_id"),
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
