"""Batched upload conversion: fewer DB round trips, bounded in-flight work.

Before, converting N attachments repeated the config/size lookup, the
per-server retention SELECT, the participant lookup for lifecycle events, and
one SELECT per created row. After, those reads are shared across the batch,
rows are hydrated in a single bounded ``IN (...)`` query, and a process-wide
semaphore caps concurrent conversions.
"""

import pytest

from src.core.artifacts import manager as manager_module
from src.core.artifacts.manager import ArtifactManager
from src.core.artifacts.repository import get_artifacts_by_ids


def _attachments(count):
    return [
        {"attachment_id": i + 1, "filename": f"file-{i + 1}.txt", "size": 10}
        for i in range(count)
    ]


class _SpySlots:
    def __init__(self):
        self.enters = 0
        self.exits = 0

    def __enter__(self):
        self.enters += 1
        return self

    def __exit__(self, *exc_info):
        self.exits += 1
        return False


def test_batch_conversion_creates_every_artifact(db, monkeypatch):
    manager = ArtifactManager(db, {})
    monkeypatch.setattr(manager, "_events_enabled", lambda: False)

    converted = manager.convert_uploads_to_artifacts(
        _attachments(3), conversation_id=100, author_id=7
    )

    assert len(converted) == 3
    assert len({artifact.id for artifact in converted}) == 3
    assert [artifact.payload["attachment_id"] for artifact in converted] == [1, 2, 3]
    assert all(manager.get(artifact.id) is not None for artifact in converted)


def test_batch_shares_retention_participant_and_hydration_reads(db, monkeypatch):
    manager = ArtifactManager(
        db, {"allow_per_server_override": True, "default_retention_days": 5}
    )
    monkeypatch.setattr(manager, "_events_enabled", lambda: True)

    participant_calls = []
    monkeypatch.setattr(
        manager,
        "_event_user_ids",
        lambda conversation_id: participant_calls.append(conversation_id) or [1, 2],
    )

    original_fetch_all = db.fetch_all
    hydration_queries = []

    def counting_fetch_all(query, params=None):
        if "WHERE id IN" in query:
            hydration_queries.append((query, params))
        return original_fetch_all(query, params)

    original_fetch_one = db.fetch_one
    retention_queries = []

    def counting_fetch_one(query, params=None):
        if "server_artifact_settings" in query:
            retention_queries.append((query, params))
        return original_fetch_one(query, params)

    monkeypatch.setattr(db, "fetch_all", counting_fetch_all)
    monkeypatch.setattr(db, "fetch_one", counting_fetch_one)

    converted = manager.convert_uploads_to_artifacts(
        _attachments(4), conversation_id=100, author_id=7, server_id=42
    )

    assert len(converted) == 4
    assert len(hydration_queries) == 1
    assert len(hydration_queries[0][1]) == 4
    assert len(retention_queries) == 1
    assert participant_calls == [100]


def test_batch_size_is_capped(db):
    manager = ArtifactManager(db, {})
    with pytest.raises(ValueError, match="more than"):
        manager.convert_uploads_to_artifacts(
            _attachments(manager_module._MAX_CONVERSION_BATCH + 1),
            conversation_id=None,
            author_id=1,
        )


def test_single_and_batch_conversions_take_a_slot(db, monkeypatch):
    manager = ArtifactManager(db, {})
    monkeypatch.setattr(manager, "_events_enabled", lambda: False)
    spy = _SpySlots()
    monkeypatch.setattr(manager_module, "_CONVERSION_SLOTS", spy)

    manager.convert_upload_to_artifact(
        {"attachment_id": 1, "size": 1}, conversation_id=None, author_id=1
    )
    assert (spy.enters, spy.exits) == (1, 1)

    manager.convert_uploads_to_artifacts(
        _attachments(2), conversation_id=None, author_id=1
    )
    assert (spy.enters, spy.exits) == (2, 2)


def test_get_artifacts_by_ids_uses_one_bounded_query(db):
    manager = ArtifactManager(db, {})
    converted = manager.convert_uploads_to_artifacts(
        _attachments(2), conversation_id=None, author_id=1
    )
    ids = [artifact.id for artifact in converted]

    fetched = get_artifacts_by_ids(db, ids + ids + ["not-an-id"])
    assert set(fetched) == {int(artifact_id) for artifact_id in ids}

    class _Boom:
        def fetch_all(self, *args, **kwargs):
            raise AssertionError("oversized batch must not reach the database")

    with pytest.raises(ValueError, match="more than"):
        get_artifacts_by_ids(_Boom(), list(range(300)))


def test_batch_still_enforces_size_cap(db, monkeypatch):
    manager = ArtifactManager(db, {"max_artifact_size_mb": 1})
    monkeypatch.setattr(manager, "_events_enabled", lambda: False)
    oversized = [{"attachment_id": 9, "size": 2 * 1024 * 1024}]

    with pytest.raises(ValueError, match="maximum size"):
        manager.convert_uploads_to_artifacts(
            oversized, conversation_id=None, author_id=1
        )
    assert manager.count() == 0
