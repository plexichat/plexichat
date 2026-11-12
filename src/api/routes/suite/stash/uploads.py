"""Stash file uploads and the upload size ceilings."""

import src.api as api
from fastapi import Depends, HTTPException, status

from src.api.middleware.authentication import TokenInfo, get_current_user
from src.api.schemas.common import ErrorResponse
from src.api.schemas.stash import (
    StashFileUploadRequest,
    StashItemResponse,
)
from src.core.media.exceptions import StorageQuotaExceededError
from .helpers import _error, _package, resolve_manager as _manager
from .router import router
from .serializers import (
    _item_response,
)

# Upload payload ceilings. The base64 (encoded) ceiling is checked before
# decoding to fail fast on absurd requests; the decoded ceiling is checked
# after decoding so a valid base64 payload cannot silently exceed the stored
# media size limits. Both must be larger than any configured media-type limit
# so the media boundary remains the authoritative gate.
MAX_UPLOAD_ENCODED_LEN = 70_000_000
MAX_UPLOAD_DECODED_BYTES = 50 * 1024 * 1024


def _limits() -> tuple[int, int]:
    """Read the upload ceilings off the package so tests can lower them."""
    pkg = _package()
    return pkg.MAX_UPLOAD_ENCODED_LEN, pkg.MAX_UPLOAD_DECODED_BYTES


@router.post(
    "/upload",
    response_model=StashItemResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Invalid request data"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        403: {"model": ErrorResponse, "description": "PlexiStash is unavailable"},
        413: {"model": ErrorResponse, "description": "File is too large"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_file(
    body: StashFileUploadRequest,
    current_user: TokenInfo = Depends(get_current_user),
) -> StashItemResponse:
    """Upload file bytes to Stash: store a media object, then reference it.

    The bytes go through the existing media storage boundary (which owns
    content-type detection, sanitization, and size limits) and the Stash item
    references the returned media object id. No filesystem path is accepted.
    """
    try:
        import base64 as base64_module

        max_encoded, max_decoded = _limits()
        raw = body.content
        if raw.startswith("data:"):
            comma = raw.find(",")
            if comma == -1 or ";base64" not in raw[:comma].lower():
                raise HTTPException(status_code=400, detail="Invalid file payload")
            encoded = raw[comma + 1 :]
        else:
            encoded = raw
        if len(encoded) > max_encoded:
            raise HTTPException(
                status_code=413,
                detail=(
                    "File is too large: the base64 payload exceeds the "
                    f"maximum encoded size of {max_encoded} bytes"
                ),
            )
        try:
            data = base64_module.b64decode(encoded, validate=True)
        except Exception as exc:
            raise HTTPException(
                status_code=400, detail="Invalid base64 payload"
            ) from exc
        if len(data) > max_decoded:
            raise HTTPException(
                status_code=413,
                detail=(
                    "File is too large: decoded content exceeds the maximum "
                    f"size of {max_decoded} bytes"
                ),
            )

        media = api.get_media()
        if media is None:
            raise HTTPException(status_code=503, detail="Media storage is unavailable")
        upload = media.upload_file(
            current_user.user_id,
            data,
            body.filename,
            body.content_type,
        )
        item = _manager().create_item(
            current_user.user_id,
            item_type="file",
            title=body.title,
            scope=body.scope,
            server_id=int(body.server_id) if body.server_id is not None else None,
            description=body.description,
            media_object_id=str(upload.file_id),
            metadata={
                "size": int(upload.size),
                "content_type": upload.content_type,
            },
            tags=body.tags,
        )
        return _item_response(item)
    except HTTPException:
        raise
    except StorageQuotaExceededError as exc:
        raise HTTPException(
            status_code=413,
            detail={
                "error": {
                    "code": 413,
                    "message": str(exc),
                    "used_bytes": exc.used_bytes,
                    "limit_bytes": exc.limit_bytes,
                    "requested_bytes": exc.requested_bytes,
                }
            },
        ) from exc
    except Exception as exc:
        raise _error(exc) from exc
