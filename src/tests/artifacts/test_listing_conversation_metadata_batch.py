"""Artifact listing must batch conversation ``metadata.server_id`` resolution.

``_artifact_response`` -> ``_my_permissions`` -> ``_conversation_server_id``
used to re-read ``msg_conversations.metadata`` once for every artifact in a
page whose caller is a conversation member. A page of N artifacts spread over
N conversations therefore issued N metadata queries (an N+1). ``list_artifacts``
now primes the shared auth cache with a single ``IN (?, ...)`` bulk read before
building responses.

This test drives the real route function and measures the reduction by toggling
only that priming step: without it the page issues N reads, with it a single
read, and the response bodies are byte-for-byte identical.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.api as api
from src.api.routes import artifacts_helpers as helpers
from src.api.routes.artifacts.routes import crud as artifacts_crud
from src.core.artifacts.models import ArtifactStatus, ArtifactType

pytestmark = [pytest.mark.asyncio]

_USER_ID = 42
_PAGE_SIZE = 12


def _artifact(artifact_id: int, conversation_id: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=artifact_id,
        conversation_id=conversation_id,
        channel_id=None,
        server_id=None,
        author_id=_USER_ID,
        artifact_type=ArtifactType.FILE,
        title="t",
        summary=None,
        status=ArtifactStatus.COMPLETED,
        recorded=False,
        has_transcript=False,
        payload={},
        retention_policy=None,
        expires_at=None,
        license_feature=None,
        created_at=1,
        updated_at=1,
        rev=0,
    )


async def test_list_artifacts_batches_conversation_metadata(db, monkeypatch):
    conv_ids = [900_000 + i for i in range(_PAGE_SIZE)]
    server_ids = [7_000 + i for i in range(_PAGE_SIZE)]
    for cid, sid in zip(conv_ids, server_ids):
        db.execute(
            "INSERT INTO msg_conversations "
            "(id, conversation_type, created_at, updated_at, metadata) "
            "VALUES (?, 'server', 0, 0, ?)",
            (cid, json.dumps({"server_id": sid})),
        )

    artifacts = [_artifact(100_000 + i, cid) for i, cid in enumerate(conv_ids)]

    class _Manager:
        def list_with_filters(self, **kwargs):
            return artifacts

        def count(self, filters):
            return len(artifacts)

    class _Messaging:
        def is_participant(self, conversation_id, user_id):
            # Membership is what drives _my_permissions into
            # _conversation_server_id for every artifact.
            return True

    class _Auth:
        def get_user_profiles_bulk(self, ids):
            return {}

    monkeypatch.setattr(api, "get_db", lambda: db)
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: _Messaging())
    monkeypatch.setattr(api, "get_auth", lambda: _Auth())
    monkeypatch.setattr(artifacts_crud, "_get_manager", lambda: _Manager())
    monkeypatch.setattr(
        artifacts_crud, "_require_artifact_capability", lambda *a, **k: "artifacts"
    )

    real_prime = artifacts_crud._prime_conversation_server_cache
    priming = {"on": False}

    def _maybe_prime(items, cache):
        if priming["on"]:
            return real_prime(items, cache)

    monkeypatch.setattr(
        artifacts_crud, "_prime_conversation_server_cache", _maybe_prime
    )

    user = SimpleNamespace(user_id=_USER_ID, username="u")

    async def _run(priming_on: bool):
        priming["on"] = priming_on
        msg_queries: list[str] = []
        orig_one, orig_all = db.fetch_one, db.fetch_all

        def _one(sql, params=None):
            if "msg_conversations" in sql:
                msg_queries.append(sql)
            return orig_one(sql, params)

        def _all(sql, params=None):
            if "msg_conversations" in sql:
                msg_queries.append(sql)
            return orig_all(sql, params)

        with (
            patch.object(db, "fetch_one", side_effect=_one),
            patch.object(db, "fetch_all", side_effect=_all),
        ):
            result = await artifacts_crud.list_artifacts(
                current_user=user,
                include_payload=False,
                limit=_PAGE_SIZE,
                offset=0,
            )
        return result, msg_queries

    unbatched, before_queries = await _run(False)
    batched, after_queries = await _run(True)

    assert len(unbatched.items) == len(batched.items) == _PAGE_SIZE
    # Identical responses: batching is a pure query optimization.
    assert [i.model_dump(mode="json") for i in unbatched.items] == [
        i.model_dump(mode="json") for i in batched.items
    ]

    # Before: one msg_conversations metadata read per distinct conversation.
    assert len(before_queries) == _PAGE_SIZE, (
        f"expected unbatched N+1 = {_PAGE_SIZE} metadata reads, "
        f"got {len(before_queries)}"
    )
    # After: a single bulk read, regardless of page size.
    assert len(after_queries) == 1, (
        f"expected 1 batched metadata read, got {len(after_queries)}: {after_queries}"
    )
    assert " IN (" in after_queries[0], (
        "batched read must use an IN (?, ...) query, not per-id lookups"
    )
    assert len(after_queries) < len(before_queries)


async def test_prime_conversation_server_cache_populates_and_skips_queries(
    db, monkeypatch
):
    """The bulk helper memoizes correct server_ids; lookups then hit the cache."""
    conv_ids = [910_000 + i for i in range(_PAGE_SIZE)]
    server_ids = [8_000 + i for i in range(_PAGE_SIZE)]
    for cid, sid in zip(conv_ids, server_ids):
        db.execute(
            "INSERT INTO msg_conversations "
            "(id, conversation_type, created_at, updated_at, metadata) "
            "VALUES (?, 'server', 0, 0, ?)",
            (cid, json.dumps({"server_id": sid})),
        )
    artifacts = [SimpleNamespace(conversation_id=cid) for cid in conv_ids]
    cache = helpers._AuthCache()
    monkeypatch.setattr(api, "get_db", lambda: db)

    helpers._prime_conversation_server_cache(artifacts, cache)

    for cid, sid in zip(conv_ids, server_ids):
        assert cache.conversation_servers[cid] == sid

    # Subsequent reads are pure dict hits: no SQL touches msg_conversations.
    with patch.object(db, "fetch_one", side_effect=AssertionError("unexpected read")):
        for cid, sid in zip(conv_ids, server_ids):
            assert helpers._conversation_server_id(cid, cache) == sid
