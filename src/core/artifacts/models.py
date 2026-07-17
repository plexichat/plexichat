"""
Artifacts domain models.

Dataclasses mapping to the `artifacts` and `voice_calls` tables defined in
`schema.py`. They mirror the style of the messaging and voice modules: pure
data containers with Snowflake IDs, boolean columns stored as ints in the DB
but exposed as `bool`/`list`/`dict` on the model.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, List, Any

from src.core.base import SnowflakeID


class ArtifactType(Enum):
    """Kind of artifact persisted in the `artifacts` table."""

    VOICE_CALL = "voice_call"
    WHITEBOARD = "whiteboard"
    UPLOAD = "upload"
    FILE = "file"
    TRANSCRIPT = "transcript"
    FUTURE = "future"


class ArtifactStatus(Enum):
    """Lifecycle state of an artifact."""

    LIVE = "live"
    COMPLETED = "completed"
    ARCHIVED = "archived"


@dataclass
class Artifact:
    """A first-class persistent record (call, whiteboard, upload, etc.)."""

    id: SnowflakeID
    conversation_id: Optional[SnowflakeID]
    channel_id: Optional[SnowflakeID]
    server_id: Optional[SnowflakeID]
    author_id: SnowflakeID
    artifact_type: ArtifactType
    title: str
    summary: Optional[str]
    status: ArtifactStatus
    recorded: bool
    has_transcript: bool
    payload: Dict[str, Any]
    created_at: int
    updated_at: int
    retention_policy: Optional[Any] = None
    expires_at: Optional[int] = None
    license_feature: Optional[str] = None


@dataclass
class VoiceCall:
    """Call-specific metadata, typically linked to an `artifacts` row."""

    id: SnowflakeID
    conversation_id: Optional[SnowflakeID]
    channel_id: Optional[SnowflakeID]
    server_id: Optional[SnowflakeID]
    initiator_id: Optional[SnowflakeID]
    started_at: int
    created_at: int
    updated_at: int
    artifact_id: Optional[SnowflakeID] = None
    ended_at: Optional[int] = None
    duration_seconds: Optional[int] = None
    recorded: bool = False
    transcript_artifact_id: Optional[SnowflakeID] = None
    consented_participants: List[int] = field(default_factory=list)
    participant_count: int = 0
