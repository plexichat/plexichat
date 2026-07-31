"""
Retention tests: purging expired artifacts cascades to ops and voice calls.
"""

import time

from src.core.artifacts.repository import (
    append_artifact_op,
    get_artifact,
    list_artifact_ops,
)
from src.core.artifacts.retention import purge_expired


def _past_ms() -> int:
    return int(time.time() * 1000) - 100_000


def test_purge_expired_removes_artifact_and_cascades(db, artifact_builder):
    expired = artifact_builder(db, author_id=1, expires_at=_past_ms(), title="expired")
    fresh = artifact_builder(db, author_id=1, expires_at=None, title="fresh")
    append_artifact_op(db, expired.id, "edit", 1, {"delta": "x"})
    db.execute(
        "INSERT INTO voice_calls "
        "(id, artifact_id, conversation_id, initiator_id, started_at, "
        " duration_seconds, recorded, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (8001, expired.id, None, 1, 1000, 5, 2000, 2000),
    )

    removed = purge_expired(db)

    assert removed == 1
    assert get_artifact(db, expired.id) is None
    assert list_artifact_ops(db, expired.id) == []
    assert db.fetch_one("SELECT id FROM voice_calls WHERE id = 8001") is None
    assert get_artifact(db, fresh.id) is not None


def test_purge_expired_leaves_future_artifacts(db, artifact_builder):
    future = artifact_builder(db, author_id=1, expires_at=_past_ms() + 10_000_000)
    assert purge_expired(db) == 0
    assert get_artifact(db, future.id) is not None


def test_purge_expired_no_expiry_is_noop(db, artifact_builder):
    artifact = artifact_builder(db, author_id=1)
    assert purge_expired(db) == 0
    assert get_artifact(db, artifact.id) is not None


def test_purge_expired_null_db_is_noop():
    assert purge_expired(None) == 0
