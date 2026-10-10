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


def test_search_escapes_like_wildcards(db, artifact_builder):
    """A bare "%" or "_" in user search must be literal, not a wildcard."""
    from src.core.artifacts.repository import list_artifacts

    match = _seed_artifact(db, artifact_builder, title="Quarterly report")
    percent = _seed_artifact(db, artifact_builder, title="100% done")
    underscore = _seed_artifact(db, artifact_builder, title="snake_case doc")

    # Wildcards are treated as literals: "%" matches only titles containing
    # an actual percent sign (previously it matched EVERY row).
    assert [int(a.id) for a in list_artifacts(db, {"search": "%"})] == [int(percent.id)]
    assert [int(a.id) for a in list_artifacts(db, {"search": "_"})] == [
        int(underscore.id)
    ]
    assert [int(a.id) for a in list_artifacts(db, {"search": "100%"})] == [
        int(percent.id)
    ]
    assert [int(a.id) for a in list_artifacts(db, {"search": "snake_"})] == [
        int(underscore.id)
    ]
    assert [int(a.id) for a in list_artifacts(db, {"search": "report"})] == [
        int(match.id)
    ]


# === optimistic concurrency on the rev column ===


def test_new_artifact_starts_at_rev_zero(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder)
    assert get_artifact(db, artifact.id).rev == 0


def test_create_seeds_rev_from_payload_rev(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder, payload={"content": "x", "rev": 4})
    assert get_artifact(db, artifact.id).rev == 4


def test_update_with_expected_rev_advances_atomically(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder)

    updated = update_artifact(db, artifact.id, expected_rev=0, payload={"content": "a"})
    assert updated is not None
    assert updated.rev == 1
    assert updated.payload == {"content": "a"}

    updated = update_artifact(db, artifact.id, expected_rev=1, payload={"content": "b"})
    assert updated is not None
    assert updated.rev == 2


def test_update_with_stale_expected_rev_returns_none(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder)
    update_artifact(db, artifact.id, expected_rev=0, payload={"content": "a"})

    # A second writer racing with the same expected rev must not clobber.
    assert (
        update_artifact(db, artifact.id, expected_rev=0, payload={"content": "stale"})
        is None
    )
    assert get_artifact(db, artifact.id).rev == 1
    assert get_artifact(db, artifact.id).payload == {"content": "a"}


def test_update_without_expected_rev_does_not_move_rev(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder, payload={"rev": 3})
    updated = update_artifact(db, artifact.id, title="renamed")
    assert updated is not None
    assert updated.rev == 3


def test_rev_not_writable_directly_through_fields(db, artifact_builder):
    artifact = _seed_artifact(db, artifact_builder)
    # ``rev`` in fields is immutable-by-direct-set: only the expected_rev path
    # moves it, so a caller can never rewind or forge the chain.
    updated = update_artifact(db, artifact.id, rev=99, title="x")
    assert updated.rev == 0


def test_update_with_expected_rev_on_missing_artifact_returns_none(db):
    assert update_artifact(db, 999_999, expected_rev=0, title="x") is None


def test_set_server_retention_days_upsert_is_idempotent(db):
    from src.core.artifacts.repository import (
        get_server_retention_days,
        set_server_retention_days,
    )

    set_server_retention_days(db, 501, 30)
    assert get_server_retention_days(db, 501) == 30
    # Repeated writes (the former SELECT/INSERT race window) stay consistent.
    set_server_retention_days(db, 501, 45)
    assert get_server_retention_days(db, 501) == 45
    set_server_retention_days(db, 501, None)
    assert get_server_retention_days(db, 501) is None


def test_set_feature_setting_upsert_is_idempotent(db):
    from src.core.artifacts.repository import (
        get_feature_settings,
        set_feature_setting,
    )

    set_feature_setting(db, 502, "whiteboard", "max_users", "10")
    set_feature_setting(db, 502, "whiteboard", "max_users", "25")
    assert get_feature_settings(db, 502, "whiteboard") == {"max_users": "25"}
    # A second key must not collide with the UNIQUE(server_id, feature, key).
    set_feature_setting(db, 502, "whiteboard", "theme", "dark")
    assert get_feature_settings(db, 502, "whiteboard") == {
        "max_users": "25",
        "theme": "dark",
    }
    set_feature_setting(db, 502, "whiteboard", "theme", None)
    assert get_feature_settings(db, 502, "whiteboard") == {"max_users": "25"}
