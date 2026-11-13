"""Workbook CRUD, templates, pinning, and archival."""

from fastapi import Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumCreateFromTemplateRequest,
    SumPinRequest,
    SumTemplateRequest,
    SumWorkbookCreateRequest,
    SumWorkbookListResponse,
    SumWorkbookResponse,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


@router.get(
    "/workbooks",
    response_model=SumWorkbookListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_workbooks(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    template: bool = Query(
        False, description="List template workbooks instead of regular workbooks"
    ),
    search: str = Query("", max_length=200, description="Title substring search"),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookListResponse:
    try:
        items, total = _manager().list_workbooks(
            current_user.user_id,
            limit,
            offset,
            template=template,
            search=search.strip() or None,
        )
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
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
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
            template=body.template,
        )
        return SumWorkbookResponse.model_validate(workbook)
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/from-template",
    response_model=SumWorkbookResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_from_template(
    body: SumCreateFromTemplateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        workbook = _manager().create_from_template(
            current_user.user_id,
            int(body.template_id),
            body.title,
            body.scope,
            int(body.server_id) if body.server_id is not None else None,
        )
        return SumWorkbookResponse.model_validate(workbook)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid template id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/workbooks/{workbook_id}/pin",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_pinned(
    workbook_id: str,
    body: SumPinRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        workbook = _manager().set_pinned(
            current_user.user_id, int(workbook_id), body.pinned
        )
        return SumWorkbookResponse.model_validate(workbook)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/workbooks/{workbook_id}/template",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def set_template(
    workbook_id: str,
    body: SumTemplateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumWorkbookResponse:
    try:
        workbook = _manager().set_template(
            current_user.user_id, int(workbook_id), body.is_template
        )
        return SumWorkbookResponse.model_validate(workbook)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.get(
    "/workbooks/{workbook_id}",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
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


@router.delete(
    "/workbooks/{workbook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
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


@router.post(
    "/workbooks/{workbook_id}/restore",
    response_model=SumWorkbookResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
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
