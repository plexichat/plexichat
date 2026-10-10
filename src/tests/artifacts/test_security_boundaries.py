"""Regression coverage for artifact authorization and lifecycle boundaries."""

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


def test_hard_delete_keeps_server_shared_artifact_but_scrubs_author(
    db, artifact_builder
):
    """L22: hard erasure must not delete content shared with a server.

    A SUM/workbook-like artifact the user authored and shared to a server is
    co-owned by the server's other members; erasing the account must scrub
    the user's identity from the row instead of destroying the document.
    """
    artifact = artifact_builder(
        db,
        author_id=41,
        artifact_type="sum",
        server_id=77,
        title="Shared Workbook",
        payload={"document": "shared with the team"},
    )

    touched = anonymize_user_artifacts(db, 41, {"anonymize_content": False})

    assert touched >= 1
    kept = get_artifact(db, artifact.id)
    assert kept is not None
    assert kept.server_id == 77
    assert kept.author_id == 0  # anonymized sentinel, row survives
    assert kept.title == "[deleted user content]"


def test_hard_delete_scrubs_text_of_shared_document(db, artifact_builder):
    """Shared content keeps its structure but loses the user's inline text."""
    artifact = artifact_builder(
        db,
        author_id=41,
        artifact_type="plexiscribe",
        server_id=88,
        title="Shared Notes",
        payload={"text": "secret scribble by user 41", "meta": {"kept": True}},
    )

    anonymize_user_artifacts(db, 41, {"anonymize_content": False})

    kept = get_artifact(db, artifact.id)
    assert kept is not None
    assert kept.server_id == 88
    payload = kept.payload or {}
    assert "text" not in payload
    assert payload.get("meta") == {"kept": True}


def test_hard_delete_unshared_artifact_is_still_removed(db, artifact_builder):
    """The L22 guard only applies to server-shared content."""
    private = artifact_builder(
        db, author_id=41, artifact_type="upload", title="private file"
    )
    shared = artifact_builder(
        db,
        author_id=41,
        artifact_type="upload",
        server_id=99,
        title="shared file",
    )

    anonymize_user_artifacts(db, 41, {"anonymize_content": False})

    assert get_artifact(db, private.id) is None
    assert get_artifact(db, shared.id) is not None


def test_hard_delete_shared_voice_call_cascade_preserves_recorded_call(
    db, artifact_builder
):
    """A recorded server call survives erasure; initiator linkage is scrubbed."""
    artifact = artifact_builder(
        db,
        author_id=41,
        artifact_type="voice_call",
        server_id=55,
        title="Team Call",
        payload={"recording_ref": "media://call-7402"},
    )
    db.execute(
        "INSERT INTO voice_calls "
        "(id, artifact_id, conversation_id, server_id, initiator_id, started_at, "
        "duration_seconds, recorded, consented_participants, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
        (7402, artifact.id, None, 55, 41, 1000, 5, "[41, 42]", 2000, 2000),
    )

    anonymize_user_artifacts(db, 41, {"anonymize_content": False})

    kept = get_artifact(db, artifact.id)
    assert kept is not None
    assert kept.server_id == 55
    row = db.fetch_one("SELECT * FROM voice_calls WHERE id = 7402")
    assert row is not None
    assert int(row["initiator_id"]) == 0


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


def test_unsubscribe_is_connection_scoped_across_tabs():
    """A user's remaining tabs keep receiving relays after one tab unsubscribes.

    Closing the artifact panel in tab A (ARTIFACT_UNSUBSCRIBE) must not drop
    the user from the artifact's subscriber set while tab B of the same user
    is still subscribed; otherwise tab B silently misses live deltas.
    """
    registry = ArtifactSubscriptionRegistry()
    registry.subscribe(10, 100, "conn-a")
    registry.subscribe(10, 100, "conn-b")
    registry.subscribe(11, 100, "conn-c")

    # Tab conn-a unsubscribes: user 10 stays subscribed via conn-b.
    registry.unsubscribe(10, 100, "conn-a")
    assert registry.get_subscribers(100) == {10, 11}

    # Last tab of user 10 unsubscribes: user 10 is now removed.
    registry.unsubscribe(10, 100, "conn-b")
    assert registry.get_subscribers(100) == {11}

    registry.unsubscribe(11, 100, "conn-c")
    assert registry.get_subscribers(100) == set()
