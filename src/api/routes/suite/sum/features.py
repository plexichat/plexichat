"""Workbook feature flags."""

from typing import Optional

from fastapi import Depends, HTTPException, Query, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.sum import (
    SumFeatureCreateRequest,
    SumFeatureListResponse,
    SumFeatureResponse,
    SumFeatureUpdateRequest,
)
from .helpers import _error, resolve_manager as _manager
from .router import router


@router.get(
    "/workbooks/{workbook_id}/features",
    response_model=SumFeatureListResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def list_features(
    workbook_id: str,
    feature_type: Optional[str] = Query(None, pattern="^(chart|pivot|sparkline)$"),
    current_user: TokenInfo = Depends(get_current_user),
) -> SumFeatureListResponse:
    try:
        features = _manager().list_features(
            current_user.user_id, int(workbook_id), feature_type
        )
        return SumFeatureListResponse(
            items=[SumFeatureResponse.model_validate(f) for f in features]
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.post(
    "/workbooks/{workbook_id}/features",
    response_model=SumFeatureResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_feature(
    workbook_id: str,
    body: SumFeatureCreateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumFeatureResponse:
    try:
        feature = _manager().create_feature(
            current_user.user_id,
            int(workbook_id),
            body.feature_type,
            body.definition,
            body.sheet_id,
        )
        return SumFeatureResponse.model_validate(feature)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid workbook id") from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.patch(
    "/workbooks/{workbook_id}/features/{feature_id}",
    response_model=SumFeatureResponse,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def update_feature(
    workbook_id: str,
    feature_id: str,
    body: SumFeatureUpdateRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> SumFeatureResponse:
    try:
        feature = _manager().update_feature(
            current_user.user_id,
            int(workbook_id),
            int(feature_id),
            definition=body.definition,
            sheet_id=body.sheet_id,
        )
        return SumFeatureResponse.model_validate(feature)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or feature id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc


@router.delete(
    "/workbooks/{workbook_id}/features/{feature_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiSum is unavailable"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def delete_feature(
    workbook_id: str,
    feature_id: str,
    current_user: TokenInfo = Depends(get_current_user),
) -> None:
    try:
        _manager().delete_feature(
            current_user.user_id, int(workbook_id), int(feature_id)
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=400, detail="Invalid workbook or feature id"
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc
