"""Regression coverage for artifact authorization and lifecycle boundaries."""

from unittest.mock import Mock

import pytest

from src.api.websocket.artifacts import ArtifactSubscriptionRegistry
from src.core.artifacts.capabilities import (
    artifact_type_capability,
    capability_allows_artifact,
)
from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.models import ArtifactType
from src.core.artifacts.privacy import anonymize_user_artifacts
from src.core.artifacts.repository import append_artifact_op, get_artifact


def test_artifact_type_capability_mapping_is_canonical():
    assert artifact_type_capability(ArtifactType.WHITEBOARD) == "artifacts_whiteboard"
    assert artifact_type_capability(ArtifactType.PLEXISCRIBE) == "plexiscribe"
    assert artifact_type_capability(ArtifactType.PLEXISCRIPT) == "plexiscript"
    assert artifact_type_capability(ArtifactType.UPLOAD) == "artifacts"


def test_base_artifact_capability_can_be_evaluated_from_config():
    config = {"enabled": True}
    assert capability_allows_artifact(ArtifactType.UPLOAD, config) is True
    assert capability_allows_artifact(ArtifactType.UPLOAD, {"enabled": False}) is False


def test_server_retention_override_recalculates_inherited_artifacts(
    db, artifact_builder
):
    manager = ArtifactManager(
        db,
        {
            "enabled": True,
            "allow_per_server_override": True,
            "default_retention_days": 30,
        },
    )
    artifact = manager.create(
        conversation_id=None,
        author_id=1,
        artifact_type=ArtifactType.UPLOAD,
        title="inherited retention",
        server_id=77,
    )

    manager.set_server_retention_days(77, 2)
    changed = get_artifact(db, artifact.id)
    assert changed is not None
    assert changed.expires_at == changed.created_at + 2 * 86400 * 1000

    manager.set_server_retention_days(77, None)
    cleared = get_artifact(db, artifact.id)
    assert cleared is not None
    assert cleared.expires_at == cleared.created_at + 30 * 86400 * 1000


def test_explicit_retention_policy_remains_authoritative_on_server_change(db):
    manager = ArtifactManager(
        db,
        {
            "enabled": True,
            "allow_per_server_override": True,
            "default_retention_days": 30,
        },
    )
    artifact = manager.create(
        conversation_id=None,
        author_id=1,
        artifact_type=ArtifactType.UPLOAD,
        title="explicit retention",
        server_id=78,
        retention_policy={"days": 7},
    )

    manager.set_server_retention_days(78, 2)
    unchanged = get_artifact(db, artifact.id)
    assert unchanged is not None
    assert unchanged.expires_at == unchanged.created_at + 7 * 86400 * 1000


def test_oversized_durable_operation_is_rejected(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1, artifact_type="upload")
    oversized = {"text": "x" * (256 * 1024)}
    assert append_artifact_op(db, artifact.id, "edit", 1, oversized) is None


def test_hard_delete_privacy_handles_cascaded_voice_call(db, artifact_builder):
    artifact = artifact_builder(
        db,
        author_id=41,
        artifact_type="transcript",
        payload={"text": "private transcript"},
    )
    db.execute(
        "INSERT INTO voice_calls "
        "(id, artifact_id, conversation_id, initiator_id, started_at, "
        "duration_seconds, recorded, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (7401, artifact.id, None, 41, 1000, 5, 2000, 2000),
    )

    touched = anonymize_user_artifacts(db, 41, {"anonymize_content": False})

    assert touched >= 1
    assert get_artifact(db, artifact.id) is None
    assert db.fetch_one("SELECT id FROM voice_calls WHERE id = 7401") is None


def test_subscription_cleanup_is_connection_scoped():
    registry = ArtifactSubscriptionRegistry()
    registry.subscribe(10, 100, "conn-a")
    registry.subscribe(10, 100, "conn-b")
    registry.subscribe(11, 100, "conn-c")

    registry.unsubscribe_connection("conn-a", 10)
    assert registry.get_subscribers(100) == {10, 11}

    registry.unsubscribe_connection("conn-b", 10)
    assert registry.get_subscribers(100) == {11}

    registry.unsubscribe_connection("conn-c", 11)
    assert registry.get_subscribers(100) == set()
