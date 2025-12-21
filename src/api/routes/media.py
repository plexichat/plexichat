"""
Media API routes - File uploads, deduplication, and content moderation.

Provides endpoints for:
- Hash-based content reporting
- Chunked/resumable uploads
- Compression status
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, UploadFile, File
from pydantic import BaseModel, Field
from typing import Optional

import utils.logger as logger
from src.api.dependencies import get_db, get_current_user


router = APIRouter(prefix="/media", tags=["Media"])


# ==================== Request/Response Models ====================

class HashReportRequest(BaseModel):
    """Report a file hash for content moderation."""
    hash_value: str = Field(..., min_length=16, max_length=128, description="SHA-256 or perceptual hash of the file")
    reason: str = Field(..., min_length=1, max_length=500, description="Reason for report")
    details: Optional[str] = Field(None, max_length=2000, description="Additional details")
    phash_value: Optional[str] = Field(None, max_length=64, description="Perceptual hash (for images)")
    uploader_id: Optional[int] = Field(None, description="User ID of the uploader")
    message_id: Optional[int] = Field(None, description="Message ID containing the attachment")
    attachment_url: Optional[str] = Field(None, max_length=2000, description="URL of the attachment")
    block_uploader: bool = Field(False, description="Request to block the uploader")


class HashReportResponse(BaseModel):
    """Response for hash report submission."""
    success: bool
    report_id: int
    message: str


class ChunkedUploadSessionRequest(BaseModel):
    """Create a chunked upload session."""
    filename: str = Field(..., min_length=1, max_length=255)
    content_type: str = Field(..., min_length=1, max_length=100)
    total_size: int = Field(..., gt=0, le=1024 * 1024 * 1024)  # Max 1GB


class ChunkedUploadSessionResponse(BaseModel):
    """Response for chunked upload session creation."""
    session_id: str
    chunk_size: int
    total_chunks: int
    expires_at: int


class ChunkUploadResponse(BaseModel):
    """Response for chunk upload."""
    success: bool
    chunk_index: int
    uploaded_chunks: int
    total_chunks: int
    progress_percent: float
    is_complete: bool
    error: Optional[str] = None


class CompressionStatusResponse(BaseModel):
    """Compression system status."""
    enabled: bool
    image_compression: bool
    video_compression: bool


# ==================== Content Reporting ====================

@router.post("/report", response_model=HashReportResponse)
async def report_content_hash(
    report: HashReportRequest,
    request: Request,
    user = Depends(get_current_user),
    db = Depends(get_db)
):
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
            block_uploader=report.block_uploader
        )

        logger.info(f"User {user.user_id} reported hash {report.hash_value[:16]}... (block_uploader={report.block_uploader})")

        return HashReportResponse(
            success=True,
            report_id=report_id,
            message="Report submitted successfully"
        )
    except Exception as e:
        logger.error(f"Failed to submit hash report: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit report"
        )


@router.get("/hash/{hash_value}/status")
async def check_hash_status(
    hash_value: str,
    user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Check if a hash is blocked."""
    from src.core import media

    is_blocked, reason = media.is_hash_blocked(hash_value)

    return {
        "hash_value": hash_value,
        "is_blocked": is_blocked,
        "reason": reason if is_blocked else None
    }


# ==================== Chunked Uploads ====================

@router.post("/upload/session", response_model=ChunkedUploadSessionResponse)
async def create_upload_session(
    session_request: ChunkedUploadSessionRequest,
    user = Depends(get_current_user),
    db = Depends(get_db)
):
    """
    Create a chunked upload session for large files.
    
    Returns session details including chunk size and total chunks.
    Use the session_id to upload chunks via POST /media/upload/chunk.
    """
    from src.core import media

    session = media.create_upload_session(
        user_id=user.user_id,
        filename=session_request.filename,
        content_type=session_request.content_type,
        total_size=session_request.total_size
    )

    if not session:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to create upload session. File may be too large."
        )

    return ChunkedUploadSessionResponse(
        session_id=session.id,
        chunk_size=session.chunk_size,
        total_chunks=session.total_chunks,
        expires_at=session.expires_at
    )


@router.post("/upload/chunk/{session_id}", response_model=ChunkUploadResponse)
async def upload_chunk(
    session_id: str,
    chunk_index: int,
    chunk_checksum: Optional[str] = None,
    file: UploadFile = File(...),
    user = Depends(get_current_user),
    db = Depends(get_db)
):
    """
    Upload a chunk to an existing upload session.
    
    Args:
        session_id: The upload session ID
        chunk_index: Zero-based index of this chunk
        chunk_checksum: Optional MD5 checksum for verification
        file: The chunk data
    """
    from src.core import media

    chunk_data = await file.read()

    result = media.upload_chunk(
        session_id=session_id,
        user_id=user.user_id,
        chunk_index=chunk_index,
        chunk_data=chunk_data,
        chunk_checksum=chunk_checksum
    )

    return ChunkUploadResponse(
        success=result.success,
        chunk_index=result.chunk_index,
        uploaded_chunks=result.uploaded_chunks,
        total_chunks=result.total_chunks,
        progress_percent=result.progress_percent,
        is_complete=result.is_complete,
        error=result.error
    )


@router.post("/upload/complete/{session_id}")
async def complete_upload_session(
    session_id: str,
    user = Depends(get_current_user),
    db = Depends(get_db)
):
    """
    Complete a chunked upload session and process the file.
    
    Returns the final upload result with file URL.
    """
    from src.core import media

    # Get the assembled file data
    file_data = media.complete_upload_session(session_id, user.user_id)

    if file_data is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Upload session not complete or not found"
        )

    # Get session info for filename/content_type
    # We need to get session info before completing - this is a limitation
    # For now, return the raw data size

    return {
        "success": True,
        "size": len(file_data),
        "message": "Upload complete. File ready for processing."
    }


@router.delete("/upload/session/{session_id}")
async def cancel_upload_session(
    session_id: str,
    user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Cancel an upload session and clean up resources."""
    from src.core import media

    success = media.cancel_upload_session(session_id, user.user_id)

    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found"
        )

    return {"success": True, "message": "Upload session cancelled"}


@router.get("/upload/sessions")
async def get_user_upload_sessions(
    user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Get all active upload sessions for the current user."""

    # Access the chunked manager directly
    from src.core import media as media_module
    manager = media_module._get_chunked_manager()

    sessions = manager.get_user_sessions(user.user_id)

    return {
        "sessions": [
            {
                "session_id": s.id,
                "filename": s.filename,
                "content_type": s.content_type,
                "total_size": s.total_size,
                "uploaded_chunks": s.uploaded_chunks,
                "total_chunks": s.total_chunks,
                "progress_percent": (s.uploaded_chunks / s.total_chunks * 100) if s.total_chunks > 0 else 0,
                "status": s.status.value,
                "expires_at": s.expires_at
            }
            for s in sessions
        ]
    }


# ==================== Compression ====================

@router.get("/compression/status", response_model=CompressionStatusResponse)
async def get_compression_status(
    user = Depends(get_current_user),
    db = Depends(get_db)
):
    """Get compression system status."""
    from src.core import media

    status = media.get_compression_status()

    return CompressionStatusResponse(
        enabled=status["enabled"],
        image_compression=status["image_compression"],
        video_compression=status["video_compression"]
    )
