"""
Media API routes - File uploads, deduplication, and content moderation.

Provides endpoints for:
- Hash-based content reporting
- Chunked/resumable uploads
- Compression status
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from typing import Optional

import utils.logger as logger
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
            success=True, report_id=report_id, message="Report submitted successfully"
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
        chunk_data = await file.read()

        result = media.upload_chunk(
            session_id=session_id,
            user_id=user.user_id,
            chunk_index=chunk_index,
            chunk_data=chunk_data,
            chunk_checksum=chunk_checksum,
        )

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
        # Get the assembled file data
        file_data = media.complete_upload_session(session_id, user.user_id)

        if file_data is None:
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

        logger.info(
            f"User {user.user_id} completed upload session {session_id} ({len(file_data)} bytes)"
        )

        return CompleteUploadResponse(
            success=True,
            size=len(file_data),
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
        return SuccessResponse(success=True)
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
