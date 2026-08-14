"""
Repository-level tests for the artifacts ops log and cascade deletes.

Covers the persistence path added for the realtime ``ARTIFACT_OP`` relay:
``append_artifact_op`` / ``list_artifact_ops``, transient-op filtering, and
the cascade delete of ``artifact_ops`` / ``voice_calls`` rows.
"""

import json

from src.core.artifacts.repository import (
    append_artifact_op,
    delete_artifact_cascade,
    get_artifact,
    list_artifact_ops,
    update_artifact,
)


def _seed_artifact(db, artifact_builder, author_id=1, **kwargs):
    return artifact_builder(db, author_id=author_id, **kwargs)


def test_append_and_list_ops_round_trip(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder)

    seq1 = append_artifact_op(db, artifact.id, "edit", 7, {"delta": "hello"})
    seq2 = append_artifact_op(db, artifact.id, "edit", 8, {"delta": "world"})

    assert seq1 == 1
    assert seq2 == 2

    ops = list_artifact_ops(db, artifact.id)
    assert len(ops) == 2
    assert ops[0]["seq"] == 1
    assert ops[0]["op_type"] == "edit"
    assert ops[0]["actor_id"] == 7
    assert ops[0]["op"] == {"delta": "hello"}
    assert ops[1]["seq"] == 2
    assert ops[1]["op"] == {"delta": "world"}
    # Ops come back with wire-compatible keys.
    assert set(ops[0].keys()) == {"seq", "op_type", "actor_id", "created_at", "op"}


def test_list_ops_after_seq(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder)
    append_artifact_op(db, artifact.id, "edit", 1, {"i": 1})
    append_artifact_op(db, artifact.id, "edit", 1, {"i": 2})

    ops = list_artifact_ops(db, artifact.id, after_seq=1)
    assert [o["seq"] for o in ops] == [2]


def test_transient_op_types_not_persisted(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder)

    assert append_artifact_op(db, artifact.id, "cursor", 1, {"x": 0, "y": 0}) is None
    assert append_artifact_op(db, artifact.id, "snapshot_request", 1, {}) is None
    assert append_artifact_op(db, artifact.id, "typing", 1, {}) is None
    assert append_artifact_op(db, artifact.id, "selection", 1, {}) is None
    assert append_artifact_op(db, artifact.id, "presence", 1, {"name": "a"}) is None
    assert append_artifact_op(db, artifact.id, "presence_leave", 1, {}) is None

    assert list_artifact_ops(db, artifact.id) == []


def test_append_op_missing_artifact_returns_none(db):
    assert append_artifact_op(db, 999_999_999, "edit", 1, {}) is None


def test_cascade_delete_removes_ops_and_voice_calls(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder)
    append_artifact_op(db, artifact.id, "edit", 1, {"delta": "x"})
    append_artifact_op(db, artifact.id, "edit", 2, {"delta": "y"})
    db.execute(
        "INSERT INTO voice_calls "
        "(id, artifact_id, conversation_id, initiator_id, started_at, "
        " duration_seconds, recorded, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (9001, artifact.id, None, 1, 1000, 12, 2000, 2000),
    )

    assert delete_artifact_cascade(db, artifact.id) is True

    assert get_artifact(db, artifact.id) is None
    assert list_artifact_ops(db, artifact.id) == []
    row = db.fetch_one("SELECT id FROM voice_calls WHERE id = 9001")
    assert row is None


def test_cascade_delete_missing_artifact_returns_false(db):
    assert delete_artifact_cascade(db, 999_999_999) is False


def test_payload_json_column_round_trip(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder, payload={"rev": 1, "lang": "en"})
    updated = update_artifact(
        db, artifact.id, payload=json.dumps({"rev": 2, "content": "hi"})
    )
    assert updated is not None
    assert updated.payload == {"rev": 2, "content": "hi"}


def test_list_artifacts_filters_by_server_id_list(db, artifact_builder):
    """``server_id`` accepts a list so the Suite shared feed can gather the
    caller's authorized servers in a single ``IN (...)`` query instead of
    one query per membership."""
    from src.core.artifacts.repository import list_artifacts

    a1 = _seed_artifact(db, artifact_builder, server_id=11)
    a2 = _seed_artifact(db, artifact_builder, server_id=22)
    a3 = _seed_artifact(db, artifact_builder, server_id=33)

    results = list_artifacts(db, {"server_id": [11, 22]})
    assert {int(a.id) for a in results} == {int(a1.id), int(a2.id)}

    # A single scalar server_id is still supported.
    results = list_artifacts(db, {"server_id": 33})
    assert [int(a.id) for a in results] == [int(a3.id)]

    # An empty list matches nothing.
    assert list_artifacts(db, {"server_id": []}) == []
