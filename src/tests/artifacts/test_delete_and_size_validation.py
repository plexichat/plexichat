"""Regression tests for L3 (delete semantics) and L4 (size validation)."""

from unittest.mock import Mock, patch

import pytest

from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.repository import get_artifact


def _manager(db):
    return ArtifactManager(db, {})


def test_delete_missing_id_returns_false(db):
    assert _manager(db).delete(999999999) is False


def test_double_delete_exactly_one_true(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1)
    mgr = _manager(db)
    first = mgr.delete(artifact.id, emit_event=False)
    second = mgr.delete(artifact.id, emit_event=False)
    assert (first, second) == (True, False)
    assert get_artifact(db, artifact.id) is None


def test_delete_purges_media_only_after_confirm(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1, payload={"recording_file_id": 55})
    with patch.object(
        ArtifactManager, "_purge_artifact_media", side_effect=RuntimeError("boom")
    ) as mock_purge:
        # Media failure must not fail the delete (best-effort).
        assert _manager(db).delete(artifact.id, emit_event=False) is True
        mock_purge.assert_called_once()
    assert get_artifact(db, artifact.id) is None


def test_delete_missing_never_purges_media(db):
    with patch.object(ArtifactManager, "_purge_artifact_media") as mock_purge:
        assert _manager(db).delete(42424242, emit_event=False) is False
        mock_purge.assert_not_called()


def test_string_size_never_500(db):
    mgr = _manager(db)
    attachment = {"attachment_id": 1, "filename": "a.bin", "size": "123"}
    try:
        artifact = mgr.convert_upload_to_artifact(
            attachment=attachment,
            conversation_id=None,
            author_id=1,
        )
    except ValueError:
        # 400 path is also acceptable; what matters is never a 500/TypeError.
        return
    except TypeError:
        pytest.fail("string size raised TypeError (would bubble as 500)")
    assert artifact is not None


def test_garbage_size_is_400_never_500(db):
    mgr = _manager(db)
    attachment = {"attachment_id": 2, "filename": "a.bin", "size": "abc"}
    with pytest.raises(ValueError, match="invalid size"):
        mgr.convert_upload_to_artifact(
            attachment=attachment,
            conversation_id=None,
            author_id=1,
        )


def test_media_module_failure_still_deletes(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1, payload={"recording_file_id": 55})
    media = Mock()
    media.delete_file = Mock(side_effect=RuntimeError("media down"))
    with patch.object(ArtifactManager, "_get_media_module", return_value=media):
        assert _manager(db).delete(artifact.id, emit_event=False) is True
    assert get_artifact(db, artifact.id) is None
