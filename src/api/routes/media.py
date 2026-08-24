"""
Media API routes - File uploads, deduplication, and content moderation.

Provides endpoints for:
- Hash-based content reporting
- Chunked/resumable uploads
- Compression status
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from typing import Optional
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

import utils.logger as logger
import src.api as api
from src.api.middleware.authentication import get_current_user, TokenInfo
from src.api.schemas.media import (
    HashReportRequest,
    HashReportResponse,
    ChunkedUploadSessionRequest,
    ChunkedUploadSessionResponse,
    ChunkUploadResponse,
    CompressionStatusResponse,
    HashStatusResponse,
    CompleteUploadResponse,
    UploadSessionsResponse,
    UploadSessionInfo,
)
from src.api.schemas.common import ErrorResponse, SuccessResponse
from src.core.media.exceptions import (
    MediaError,
    ProxyError,
    ProxyFetchError,
)

router = APIRouter(prefix="/media", tags=["Media"])


# === Content Reporting ===


@router.post(
    "/report",
    response_model=HashReportResponse,
    summary="Report content hash",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def report_content_hash(
    report: HashReportRequest,
    request: Request,
    user: TokenInfo = Depends(get_current_user),
) -> HashReportResponse:
    """
    Report a file hash for content moderation.

    This allows users to report potentially harmful content by its hash.
    Multiple reports on the same hash may trigger automatic blocking.
    Supports both SHA-256 and perceptual hashes for image similarity detection.
    """
    from src.core import media

    try:
        report_id = media.report_hash(
            hash_value=report.hash_value,
            reporter_id=user.user_id,
            reason=report.reason,
            details=report.details,
            phash_value=report.phash_value,
            uploader_id=report.uploader_id,
            message_id=report.message_id,
            attachment_url=report.attachment_url,
            block_uploader=report.block_uploader,
        )

        logger.info(
            f"User {user.user_id} reported hash {report.hash_value[:16]}... (block_uploader={report.block_uploader})"
        )

        return HashReportResponse(
            success=True,
            report_id=str(report_id),
            message="Report submitted successfully",
        )
    except Exception as e:
        logger.error(
            f"Failed to submit hash report for user {user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/hash/{hash_value}/status",
    response_model=HashStatusResponse,
    summary="Check hash status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def check_hash_status(
    hash_value: str, user: TokenInfo = Depends(get_current_user)
) -> HashStatusResponse:
    """Check if a hash is blocked."""
    from src.core import media

    try:
        is_blocked, reason = media.is_hash_blocked(hash_value)
        logger.debug(
            f"User {user.user_id} checked status for hash {hash_value[:16]}... (is_blocked={is_blocked})"
        )

        return HashStatusResponse(
            hash_value=hash_value,
            is_blocked=is_blocked,
            reason=reason if is_blocked else None,
        )
    except Exception as e:
        logger.error(
            f"Failed to check hash status for user {user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Chunked Uploads ====================


@router.post(
    "/upload/session",
    response_model=ChunkedUploadSessionResponse,
    summary="Create upload session",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Invalid request or file too large",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def create_upload_session(
    session_request: ChunkedUploadSessionRequest,
    user: TokenInfo = Depends(get_current_user),
) -> ChunkedUploadSessionResponse:
    """
    Create a chunked upload session for large files.

    Returns session details including chunk size and total chunks.
    Use the session_id to upload chunks via POST /media/upload/chunk.
    """
    from src.core import media

    try:
        session = media.create_upload_session(
            user_id=user.user_id,
            filename=session_request.filename,
            content_type=session_request.content_type,
            total_size=session_request.total_size,
        )

        if not session:
            logger.warning(
                f"Failed to create upload session for user {user.user_id} (file too large or invalid)"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Failed to create upload session. File may be too large.",
                    }
                },
            )

        logger.info(
            f"User {user.user_id} created upload session {session.id} for '{session_request.filename}'"
        )

        return ChunkedUploadSessionResponse(
            session_id=session.id,
            chunk_size=session.chunk_size,
            total_chunks=session.total_chunks,
            expires_at=session.expires_at,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to create upload session for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/upload/chunk/{session_id}",
    response_model=ChunkUploadResponse,
    summary="Upload chunk",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid chunk or session"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def upload_chunk(
    session_id: str,
    chunk_index: int,
    chunk_checksum: Optional[str] = None,
    file: UploadFile = File(...),
    user: TokenInfo = Depends(get_current_user),
) -> ChunkUploadResponse:
    """
    Upload a chunk to an existing upload session.

    Args:
        session_id: The upload session ID
        chunk_index: Zero-based index of this chunk
        chunk_checksum: Optional MD5 checksum for verification
        file: The chunk data
    """
    from src.core import media

    try:

        def _upload_chunk_stream():
            stream = file.file
            stream.seek(0)
            return media.upload_chunk_stream(
                session_id=session_id,
                user_id=user.user_id,
                chunk_index=chunk_index,
                chunk_stream=stream,
                chunk_checksum=chunk_checksum,
            )

        result = await run_in_threadpool(_upload_chunk_stream)

        if not result.success:
            logger.warning(
                f"Chunk upload failed for session {session_id}, user {user.user_id}, index {chunk_index}: {result.error}"
            )
        else:
            logger.debug(
                f"User {user.user_id} uploaded chunk {chunk_index} for session {session_id} ({result.progress_percent}%)"
            )

        return ChunkUploadResponse(
            success=result.success,
            chunk_index=result.chunk_index,
            uploaded_chunks=result.uploaded_chunks,
            total_chunks=result.total_chunks,
            progress_percent=result.progress_percent,
            is_complete=result.is_complete,
            error=result.error,
        )
    except ValueError as e:
        logger.warning(
            f"Invalid chunk upload request for session {session_id}, user {user.user_id}: {e}"
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(
            f"Chunk upload failed for session {session_id}, user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.post(
    "/upload/complete/{session_id}",
    response_model=CompleteUploadResponse,
    summary="Complete upload session",
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Upload session not complete or not found",
        },
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def complete_upload_session(
    session_id: str, user: TokenInfo = Depends(get_current_user)
) -> CompleteUploadResponse:
    """
    Complete a chunked upload session and process the file.

    Returns the final upload result with file URL.
    """
    from src.core import media

    try:
        session = media.complete_upload_session_stream(session_id, user.user_id)

        if session is None:
            logger.warning(
                f"Failed to complete upload session {session_id} for user {user.user_id} (not complete or not found)"
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "error": {
                        "code": 400,
                        "message": "Upload session not complete or not found",
                    }
                },
            )

        try:

            def _upload_complete_file():
                assert session.temp_path is not None
                with open(session.temp_path, "rb") as f:
                    return media.upload_stream(
                        user_id=user.user_id,
                        stream=f,
                        filename=session.filename,
                        content_type=session.content_type,
                        size=session.total_size,
                    )

            await run_in_threadpool(_upload_complete_file)
        finally:
            try:
                media.cancel_upload_session(session_id, user.user_id)
            except Exception:
                pass

        logger.info(
            f"User {user.user_id} completed upload session {session_id} ({session.total_size} bytes)"
        )

        return CompleteUploadResponse(
            success=True,
            size=session.total_size,
            message="Upload complete. File ready for processing.",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to complete upload session {session_id} for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.delete(
    "/upload/session/{session_id}",
    response_model=SuccessResponse,
    summary="Cancel upload session",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Session not found"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def cancel_upload_session(
    session_id: str, user: TokenInfo = Depends(get_current_user)
) -> SuccessResponse:
    """Cancel an upload session and clean up resources."""
    from src.core import media

    try:
        success = media.cancel_upload_session(session_id, user.user_id)

        if not success:
            logger.warning(
                f"Failed to cancel upload session {session_id} for user {user.user_id} (not found)"
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": 404, "message": "Session not found"}},
            )

        logger.info(f"User {user.user_id} cancelled upload session {session_id}")
        return SuccessResponse(success=True, message=None)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"Failed to cancel upload session {session_id} for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


@router.get(
    "/upload/sessions",
    response_model=UploadSessionsResponse,
    summary="Get my upload sessions",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_user_upload_sessions(
    user: TokenInfo = Depends(get_current_user),
) -> UploadSessionsResponse:
    """Get all active upload sessions for the current user."""

    # Access the chunked manager directly
    from src.core import media as media_module

    try:
        manager = media_module._get_chunked_manager()
        sessions = manager.get_user_sessions(user.user_id)
        logger.debug(f"User {user.user_id} retrieved {len(sessions)} upload sessions")

        return UploadSessionsResponse(
            sessions=[
                UploadSessionInfo(
                    session_id=s.id,
                    filename=s.filename,
                    content_type=s.content_type,
                    total_size=s.total_size,
                    uploaded_chunks=s.uploaded_chunks,
                    total_chunks=s.total_chunks,
                    progress_percent=(s.uploaded_chunks / s.total_chunks * 100)
                    if s.total_chunks > 0
                    else 0,
                    status=s.status.value,
                    expires_at=s.expires_at,
                )
                for s in sessions
            ]
        )
    except Exception as e:
        logger.error(
            f"Failed to get upload sessions for user {user.user_id}: {e}", exc_info=True
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== External URL Proxy ====================


@router.get(
    "/proxy",
    summary="Proxy external content",
    responses={
        200: {
            "description": "The proxied content (images only)",
            "content": {
                "image/jpeg": {},
                "image/png": {},
                "image/gif": {},
                "image/webp": {},
            },
        },
        400: {"model": ErrorResponse, "description": "Invalid or blocked URL"},
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        404: {"model": ErrorResponse, "description": "Proxy not available"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_proxied_content(
    url: str,
    user: TokenInfo = Depends(get_current_user),
) -> Response:
    """
    Fetch and serve external image content through Plexichat.

    The external URL is fetched server-side (with SSRF validation, DNS
    rebinding protection, content-type allow-listing and SVG/magic-byte
    rejection) and served to the client so the origin never learns the
    client's address. Results are cached by the media proxy.
    """
    if not url or len(url) > 2048:
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": "Invalid URL"}},
        )

    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail={
                "error": {"code": 400, "message": "Only http/https URLs are allowed"}
            },
        )

    # SECURITY (fetch-amplification defence): every request for an
    # uncached URL makes the server perform an authenticated outbound
    # fetch of up to the configured body-size cap against an arbitrary
    # host. Without a per-user cap any account can turn the server into
    # a bandwidth-draining fetch amplifier by cycling distinct URLs
    # (the cache only helps repeats). Enforce a per-user fixed-window
    # limit on proxy requests, sized generously so normal rendering
    # traffic is unaffected.
    from src.core.database.cache import check_rate_limit

    import utils.config as _config

    _proxy_cfg = _config.get("media", {}).get("proxy_rate_limit", {})
    _limit = int(_proxy_cfg.get("requests_per_window", 120))
    _window = int(_proxy_cfg.get("window_seconds", 60))

    allowed, _remaining = check_rate_limit(
        f"media:proxy:user:{user.user_id}", _limit, _window
    )
    if not allowed:
        logger.warning(f"Media proxy rate limit exceeded for user {user.user_id}")
        raise HTTPException(
            status_code=429,
            detail={
                "error": {
                    "code": 429,
                    "message": "Too many proxied media requests; retry later",
                }
            },
        )

    try:
        media_mod = api.get_media()
        if not media_mod:
            raise HTTPException(
                status_code=404,
                detail={"error": {"code": 404, "message": "Proxy not available"}},
            )

        data, content_type = await run_in_threadpool(media_mod.get_proxied_content, url)
        return Response(content=data, media_type=content_type)
    except HTTPException:
        raise
    except (ProxyFetchError,) as e:
        logger.warning(f"Proxy fetch failed for {url}: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except (ProxyError, MediaError) as e:
        logger.warning(f"Proxy error for {url}: {e}")
        raise HTTPException(
            status_code=400,
            detail={"error": {"code": 400, "message": str(e)}},
        )
    except Exception as e:
        logger.error(
            f"Unexpected error proxying {url} for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )


# ==================== Compression ====================


@router.get(
    "/compression/status",
    response_model=CompressionStatusResponse,
    summary="Get compression status",
    responses={
        401: {"model": ErrorResponse, "description": "Invalid or expired token"},
        500: {"model": ErrorResponse, "description": "Internal server error"},
    },
)
async def get_compression_status(
    user: TokenInfo = Depends(get_current_user),
) -> CompressionStatusResponse:
    """Get compression system status."""
    from src.core import media

    try:
        status_data = media.get_compression_status()
        logger.debug(f"User {user.user_id} requested compression status")

        return CompressionStatusResponse(
            enabled=status_data["enabled"],
            image_compression=status_data["image_compression"],
            video_compression=status_data["video_compression"],
            pillow_available=status_data["pillow_available"],
            ffmpeg_available=status_data["ffmpeg_available"],
            ffprobe_available=status_data["ffprobe_available"],
        )
    except Exception as e:
        logger.error(
            f"Failed to get compression status for user {user.user_id}: {e}",
            exc_info=True,
        )
        raise HTTPException(
            status_code=500,
            detail={"error": {"code": 500, "message": "Internal server error"}},
        )
