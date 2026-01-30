from pydantic import BaseModel, Field, ConfigDict
from typing import Optional, List


class HashReportRequest(BaseModel):
    """Report a file hash for content moderation."""

    model_config = ConfigDict(from_attributes=True)

    hash_value: str = Field(
        ...,
        min_length=16,
        max_length=128,
        description="SHA-256 or perceptual hash of the file",
    )
    reason: str = Field(
        ..., min_length=1, max_length=500, description="Reason for report"
    )
    details: Optional[str] = Field(
        None, max_length=2000, description="Additional details"
    )
    phash_value: Optional[str] = Field(
        None, max_length=64, description="Perceptual hash (for images)"
    )
    uploader_id: Optional[int] = Field(None, description="User ID of the uploader")
    message_id: Optional[int] = Field(
        None, description="Message ID containing the attachment"
    )
    attachment_url: Optional[str] = Field(
        None, max_length=2000, description="URL of the attachment"
    )
    block_uploader: bool = Field(False, description="Request to block the uploader")


class HashReportResponse(BaseModel):
    """Response for hash report submission."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether report was successfully submitted")
    report_id: str = Field(..., description="Generated report ID")
    message: str = Field(..., description="Success or info message")


class ChunkedUploadSessionRequest(BaseModel):
    """Create a chunked upload session."""

    model_config = ConfigDict(from_attributes=True)

    filename: str = Field(
        ..., min_length=1, max_length=255, description="Original filename"
    )
    content_type: str = Field(
        ..., min_length=1, max_length=100, description="MIME type of the file"
    )
    total_size: int = Field(
        ...,
        gt=0,
        le=1024 * 1024 * 1024,
        description="Total file size in bytes (max 1GB)",
    )


class ChunkedUploadSessionResponse(BaseModel):
    """Response for chunked upload session creation."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str = Field(..., description="Unique upload session ID")
    chunk_size: int = Field(..., description="Expected chunk size in bytes")
    total_chunks: int = Field(..., description="Total number of chunks expected")
    expires_at: int = Field(..., description="Session expiration timestamp (Unix)")


class ChunkUploadResponse(BaseModel):
    """Response for chunk upload."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(..., description="Whether chunk was successfully uploaded")
    chunk_index: int = Field(..., description="Index of the uploaded chunk")
    uploaded_chunks: int = Field(..., description="Number of chunks uploaded so far")
    total_chunks: int = Field(..., description="Total number of chunks in session")
    progress_percent: float = Field(
        ..., description="Overall upload progress percentage"
    )
    is_complete: bool = Field(..., description="Whether all chunks have been uploaded")
    error: Optional[str] = Field(None, description="Error message if upload failed")


class CompressionStatusResponse(BaseModel):
    """Compression system status."""

    model_config = ConfigDict(from_attributes=True)

    enabled: bool = Field(..., description="Whether compression is enabled globally")
    image_compression: bool = Field(
        ..., description="Whether image compression is enabled"
    )
    video_compression: bool = Field(
        ..., description="Whether video compression is enabled"
    )


class HashStatusResponse(BaseModel):
    """Status of a file hash."""

    model_config = ConfigDict(from_attributes=True)

    hash_value: str = Field(..., description="The hash value checked")
    is_blocked: bool = Field(..., description="Whether the hash is blocked")
    reason: Optional[str] = Field(None, description="Reason for blocking if blocked")


class CompleteUploadResponse(BaseModel):
    """Response for completed upload session."""

    model_config = ConfigDict(from_attributes=True)

    success: bool = Field(
        ..., description="Whether the upload was completed successfully"
    )
    size: int = Field(..., description="Total size of the assembled file")
    message: str = Field(..., description="Status message")


class UploadSessionInfo(BaseModel):
    """Information about an active upload session."""

    model_config = ConfigDict(from_attributes=True)

    session_id: str = Field(..., description="Unique upload session ID")
    filename: str = Field(..., description="Original filename")
    content_type: str = Field(..., description="MIME type")
    total_size: int = Field(..., description="Total file size in bytes")
    uploaded_chunks: int = Field(..., description="Number of chunks uploaded")
    total_chunks: int = Field(..., description="Total number of chunks")
    progress_percent: float = Field(..., description="Overall progress percentage")
    status: str = Field(
        ..., description="Session status (pending, uploading, complete, error)"
    )
    expires_at: int = Field(..., description="Session expiration timestamp (Unix)")


class UploadSessionsResponse(BaseModel):
    """List of active upload sessions."""

    model_config = ConfigDict(from_attributes=True)

    sessions: List[UploadSessionInfo] = Field(..., description="Active upload sessions")
