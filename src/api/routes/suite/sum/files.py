"""Workbook file download/upload and CSV import/export."""

from src.core.security.hardening import parse_id
from fastapi import Depends, HTTPException

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.routes.users.helpers import require_user_token
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumCsvExportResponse,
    SumCsvImportRequest,
    SumCsvImportResponse,
    SumWorkbookFileResponse,
    SumWorkbookUploadRequest,
    SumWorkbookUploadResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


@router.get(
    "/workbooks/{workbook_id}/file",
    response_model=SumWorkbookFileResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def download_file(
    workbook_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookFileResponse:
    """Download the current ``.plexisum`` workbook file (authorization rechecked)."""
    # A bot token's ``user_id`` is its human owner, and the manager below
    # authorizes this read by comparing that value to sum_workbooks.owner_id.
    # Without this gate a bot credential is handed the owner's private file.
    require_user_token(current_user)
    try:
        result = _manager().download_file(
            current_user.user_id, parse_id(workbook_id, "workbook_id")
        )
        return SumWorkbookFileResponse.model_validate(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/file",
    response_model=SumWorkbookUploadResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_file(
    workbook_id: str,
    body: SumWorkbookUploadRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookUploadResponse:
    """Replace the workbook file with an uploaded ``.plexisum`` file."""
    try:
        import base64 as base64_module

        raw = body.content
        if raw.startswith("data:"):
            # Accept only a base64 data URL. Do not silently discard invalid
            # characters: permissive decoding can turn malformed input into a
            # different valid SQLite file.
            comma = raw.find(",")
            if comma == -1 or ";base64" not in raw[:comma].lower():
                raise HTTPException(status_code=400, detail="Invalid file payload")
            encoded = raw[comma + 1 :]
        else:
            encoded = raw
        if len(encoded) > 70_000_000:
            raise HTTPException(status_code=413, detail="Workbook file is too large")
        try:
            raw = base64_module.b64decode(encoded, validate=True)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="Invalid base64 payload"
            ) from exc
        if len(raw) > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Workbook file is too large")
        result = _manager().upload_file(
            current_user.user_id,
            parse_id(workbook_id, "workbook_id"),
            raw,
            body.base_revision,
        )
        return SumWorkbookUploadResponse.model_validate(result)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/csv",
    response_model=SumCsvExportResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def export_csv(
    workbook_id: str,
    sheet_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumCsvExportResponse:
    # See download_file: the manager authorizes on ``current_user.user_id``,
    # which for a bot is the owner. Reject bot credentials up front.
    require_user_token(current_user)
    try:
        return SumCsvExportResponse.model_validate(
            _manager().export_csv(
                current_user.user_id,
                parse_id(workbook_id, "workbook_id"),
                parse_id(sheet_id, "sheet_id"),
            )
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or sheet id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/sheets/{sheet_id}/csv",
    response_model=SumCsvImportResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def import_csv(
    workbook_id: str,
    sheet_id: str,
    body: SumCsvImportRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumCsvImportResponse:
    try:
        return SumCsvImportResponse.model_validate(
            _manager().import_csv(
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
