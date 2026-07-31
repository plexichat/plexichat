"""
Manager-level tests for cascade delete (with media purge) and the ops log.
"""

from unittest.mock import Mock, patch

from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.repository import get_artifact, list_artifact_ops


def _manager(db):
    return ArtifactManager(db, {})


def test_manager_delete_cascades(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1, payload={"recording_file_id": 55})
    _manager(db).append_op(artifact.id, "edit", 1, {"delta": "x"})
    db.execute(
        "INSERT INTO voice_calls "
        "(id, artifact_id, conversation_id, initiator_id, started_at, "
        " duration_seconds, recorded, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (7001, artifact.id, None, 1, 1000, 5, 2000, 2000),
    )

    with patch.object(ArtifactManager, "_purge_artifact_media") as mock_purge:
        assert _manager(db).delete(artifact.id) is True
        mock_purge.assert_called_once()

    assert get_artifact(db, artifact.id) is None
    assert list_artifact_ops(db, artifact.id) == []
    assert db.fetch_one("SELECT id FROM voice_calls WHERE id = 7001") is None


def test_manager_delete_without_media_purge(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1, payload={"recording_file_id": 55})
    with patch.object(ArtifactManager, "_purge_artifact_media") as mock_purge:
        assert _manager(db).delete(artifact.id, purge_media=False) is True
        mock_purge.assert_not_called()


def test_manager_append_op_passthrough(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1)
    seq = _manager(db).append_op(artifact.id, "stroke", 9, {"x": 1})
    assert seq == 1
    ops = _manager(db).list_ops(artifact.id, after_seq=0)
    assert ops[0]["op_type"] == "stroke"
    assert ops[0]["actor_id"] == 9


def test_manager_media_purge_best_effort_on_missing_module(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1, payload={"recording_file_id": 55})
    with patch.object(ArtifactManager, "_get_media_module", return_value=None):
        assert _manager(db).delete(artifact.id) is True


def test_manager_media_purge_uses_delete_file(db, artifact_builder):
    artifact = artifact_builder(
        db,
        author_id=1,
        payload={"recording_file_id": 55, "recording_file_ids": [56, 57]},
    )
    media = Mock()
    media.delete_file = Mock(side_effect=[None, None, None])
    with patch.object(ArtifactManager, "_get_media_module", return_value=media):
        _manager(db).delete(artifact.id)
    assert media.delete_file.call_count == 3
