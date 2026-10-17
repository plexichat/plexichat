"""
Shared fixtures for the artifacts test suite.

The root ``db`` fixture runs migration 000, which already creates the
``artifacts``, ``artifact_ops``, ``voice_calls``, and settings tables. This
conftest only adds small helpers for building :class:`Artifact` rows.
"""

import time
import uuid

import pytest

from src.core.artifacts.models import (
    Artifact,
    ArtifactStatus,
    ArtifactType,
)
from src.core.artifacts.repository import create_artifact


@pytest.fixture
def artifact_builder():
    """Build and persist an :class:`Artifact` row against the test db."""

    def _build(
        db,
        author_id: int,
        artifact_type: str = "whiteboard",
        server_id=None,
        conversation_id=None,
        title="Test Artifact",
        payload=None,
        expires_at=None,
        retention_policy=None,
    ) -> Artifact:
        now = int(time.time() * 1000)
        artifact = Artifact(
            id=uuid.uuid4().int & ((1 << 63) - 1),
            conversation_id=conversation_id,
            channel_id=None,
            server_id=server_id,
            author_id=author_id,
            artifact_type=ArtifactType(artifact_type),
            title=title,
            summary=None,
            status=ArtifactStatus.LIVE,
            recorded=False,
            has_transcript=False,
            payload=payload or {},
            created_at=now,
            updated_at=now,
            retention_policy=retention_policy,
            expires_at=expires_at,
            license_feature=None,
        )
        return create_artifact(db, artifact)

    return _build
