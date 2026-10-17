"""
Artifact schemas - Pydantic models for the Artifacts REST API.
"""

import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, field_validator

from src.api.schemas.common import SnowflakeID
from src.core.artifacts.models import ArtifactType, ArtifactStatus

_MAX_ARTIFACT_PAYLOAD_BYTES = 1024 * 1024


class ArtifactResponse(BaseModel):
    """Response model for a single artifact."""

    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: SnowflakeID = Field(..., description="Artifact Snowflake ID")
    conversation_id: Optional[SnowflakeID] = Field(
        None, description="Owning conversation ID"
    )
    channel_id: Optional[SnowflakeID] = Field(None, description="Owning channel ID")
    server_id: Optional[SnowflakeID] = Field(None, description="Owning server ID")
    author_id: SnowflakeID = Field(..., description="User who created the artifact")
    author_username: Optional[str] = Field(
        None, description="Username of the artifact author (server-resolved)"
    )
    author_avatar_url: Optional[str] = Field(
        None, description="Avatar URL of the artifact author (server-resolved)"
    )
    artifact_type: str = Field(..., description="Artifact type")
    title: str = Field(..., description="Display title")
    summary: Optional[str] = Field(None, description="Short summary")
    status: str = Field(..., description="Lifecycle status")
    recorded: bool = Field(False, description="Whether the artifact was recorded")
    has_transcript: bool = Field(False, description="Whether a transcript exists")
    payload: Dict[str, Any] = Field(
        default_factory=dict, description="Type-specific payload"
    )
    retention_policy: Optional[Any] = Field(
        None, description="Per-artifact retention override"
    )
    expires_at: Optional[int] = Field(None, description="Expiry timestamp (ms)")
    license_feature: Optional[str] = Field(
        None, description="License feature gating the artifact"
    )
    created_at: int = Field(..., description="Creation timestamp (ms)")
    updated_at: int = Field(..., description="Last-update timestamp (ms)")
    rev: int = Field(
        0,
        description=(
            "Monotonic revision for optimistic concurrency: the PATCH route "
            "rejects a write whose expected rev does not match this value."
        ),
    )
    my_permissions: Optional[Dict[str, Any]] = Field(
        None,
        description=(
            "Authoritative permissions for the requesting user, computed "
            "server-side (can_edit / can_view)."
        ),
    )


class _PayloadSizeMixin:
    @field_validator("payload")
    @classmethod
    def _validate_payload_size(cls, value):
        if value is None:
            return value
        try:
            size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise ValueError("payload must be JSON-serializable") from exc
        if size > _MAX_ARTIFACT_PAYLOAD_BYTES:
            raise ValueError("payload exceeds maximum size of 1 MB")
        return value


class ArtifactCreateRequest(_PayloadSizeMixin, BaseModel):
    """Request body for creating an artifact."""

    conversation_id: Optional[SnowflakeID] = Field(
        None, description="Conversation the artifact belongs to"
    )
    channel_id: Optional[SnowflakeID] = Field(
        None, description="Channel the artifact belongs to"
    )
    server_id: Optional[SnowflakeID] = Field(
        None, description="Server the artifact belongs to"
    )
    artifact_type: ArtifactType = Field(..., description="Artifact type")
    title: str = Field(..., description="Display title", min_length=1, max_length=500)
    summary: Optional[str] = Field(None, description="Short summary", max_length=2000)
    payload: Optional[Dict[str, Any]] = Field(None, description="Type-specific payload")
    status: Optional[ArtifactStatus] = Field(
        None, description="Lifecycle status (default: completed)"
    )
    recorded: bool = Field(False, description="Whether the artifact was recorded")
    has_transcript: bool = Field(False, description="Whether a transcript exists")
    license_feature: Optional[str] = Field(
        None, description="License feature gating the artifact"
    )
    retention_policy: Optional[Any] = Field(
        None, description="Per-artifact retention override (days or dict)"
    )


class ArtifactUpdateRequest(_PayloadSizeMixin, BaseModel):
    """Request body for updating an artifact."""

    title: Optional[str] = Field(
        None, description="Display title", min_length=1, max_length=500
    )
    summary: Optional[str] = Field(None, description="Short summary", max_length=2000)
    status: Optional[ArtifactStatus] = Field(None, description="Lifecycle status")
    payload: Optional[Dict[str, Any]] = Field(None, description="Type-specific payload")
    recorded: Optional[bool] = Field(
        None, description="Whether the artifact was recorded"
    )
    has_transcript: Optional[bool] = Field(
        None, description="Whether a transcript exists"
    )
    rev: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Expected current revision for optimistic concurrency. When "
            "supplied the write succeeds only if the artifact is still at this "
            "revision, and the revision advances by one; a mismatch is "
            "rejected with 409 Conflict."
        ),
    )
    retention_policy: Optional[Any] = Field(
        None, description="Per-artifact retention override"
    )


class ConvertUploadRequest(BaseModel):
    """Request body for converting an attachment to an artifact."""

    attachment_id: SnowflakeID = Field(
        ..., description="Existing attachment to convert"
    )
    conversation_id: Optional[SnowflakeID] = Field(
        None, description="Conversation the artifact belongs to"
    )
    channel_id: Optional[SnowflakeID] = Field(
        None, description="Channel the artifact belongs to"
    )
    server_id: Optional[SnowflakeID] = Field(
        None, description="Server the artifact belongs to"
    )
    title: Optional[str] = Field(None, description="Display title override")


class ArtifactListResponse(BaseModel):
    """Paginated list response for artifacts."""

    items: List[ArtifactResponse] = Field(
        default_factory=list, description="Returned artifacts"
    )
    total: int = Field(0, description="Total matching artifacts")
    has_more: bool = Field(False, description="Whether more results exist")


class RetentionPurgeResponse(BaseModel):
    """Response for a retention purge run."""

    purged: int = Field(0, description="Number of expired artifacts removed")


class ServerRetentionRequest(BaseModel):
    """Request body for setting a per-server retention override."""

    server_id: str = Field(..., description="Target server id")
    retention_days: Optional[int] = Field(
        None,
        description=(
            "Per-server retention override in days. "
            "None clears the override (falls back to global default)."
        ),
    )


class ServerRetentionResponse(BaseModel):
    """Response for a per-server retention override set/clear."""

    server_id: str = Field(..., description="Server id")
    retention_days: Optional[int] = Field(
        None, description="Effective override after the operation (None = cleared)"
    )
    success: bool = Field(True, description="Whether the operation succeeded")
