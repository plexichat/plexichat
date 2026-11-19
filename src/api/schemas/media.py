import re

from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Optional, List

from src.api.schemas.common import SnowflakeID, _reject_ui_text_chars
from src.api.schemas.policy_types import reject_unsafe_unicode
from src.api.schemas.url_types import HttpUrl
from src.core.security.outbound import resolve_outbound_url

# A perceptual hash is a fixed-width hex digest (most algorithms emit 16 or 64
# hex chars). Anchor the pattern so a value merely *containing* hex characters
# cannot pass, and so no control/format character can enter the hash pipeline.
_PHASH_HEX_RE = re.compile(r"^[0-9a-fA-F]{1,64}$")


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
    uploader_id: Optional[SnowflakeID] = Field(
        None, description="User ID of the uploader"
    )
    message_id: Optional[SnowflakeID] = Field(
        None, description="Message ID containing the attachment"
    )
    attachment_url: Optional[HttpUrl] = Field(
        None, max_length=2000, description="URL of the attachment"
    )
    block_uploader: bool = Field(False, description="Request to block the uploader")

    @field_validator("reason", "details")
    @classmethod
    def _no_ui_xss_chars(cls, value: Optional[str], info) -> Optional[str]:
        if value is None:
            return value
        return _reject_ui_text_chars(value, info.field_name)

    @field_validator("hash_value")
    @classmethod
    def _no_hash_control_chars(cls, value: str) -> str:
        """Keep control/bidi characters out of a persisted hash and its queries.

        ``hash_value`` is stored in ``media_hash_reports``, used as a lookup key
        in ``is_hash_blocked`` / ``block_hash`` and interpolated into log lines.
        Plain hex digests (and any other control-char-free value within the
        declared length bounds) are unaffected; C0/C1 controls, bidi overrides
        and invisible format characters are not.
        """
        return reject_unsafe_unicode(value, "hash_value")

    @field_validator("phash_value")
    @classmethod
    def _phash_is_hex(cls, value: Optional[str]) -> Optional[str]:
        """A perceptual hash must be an anchored hex string (or empty to clear)."""
        if not value:
            return value
        if not _PHASH_HEX_RE.fullmatch(value):
            raise ValueError("phash_value must be an anchored hex string of at most 64 characters")
        return value

    @field_validator("attachment_url")
    @classmethod
    def _no_ssrf_attachment_url(cls, value: Optional[str]) -> Optional[str]:
        """SSRF-gate attacker-controlled attachment URLs.

        The URL is stored and may be fetched by moderators or rendered in
        reports, so it passes the shared outbound gate (scheme allow-list, DNS
        resolution, private/loopback/link-local blocking) rather than a local
        copy of the scheme check.
        """
        return resolve_outbound_url(value, "Attachment URL")


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
    pillow_available: bool = Field(
        False, description="Whether the optional Pillow dependency is installed"
    )
    ffmpeg_available: bool = Field(
        False, description="Whether the optional ffmpeg dependency is installed"
    )
    ffprobe_available: bool = Field(
        False, description="Whether the optional ffprobe dependency is installed"
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
