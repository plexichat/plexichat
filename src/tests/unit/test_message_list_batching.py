"""Regression tests for batched enrichment on the message list path.

BEFORE this change a 100-message page issued one ``are_embeds_suppressed()``
SQL query per message, and a JSON-string attachment metadata value forced one
``get_file_by_filename()`` query per attachment during serialization.  These
tests pin the batched behaviour so the query fan-out cannot creep back in.
"""

import pytest

import src.api as api
from src.api.routes.messages.helpers import (
    _MAX_EMBED_SUPPRESSION_CHUNK,
    _message_to_response,
    batch_embed_suppressed_ids,
)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, params))
        return self.rows


class _FakeEmbeds:
    def __init__(self, db):
        self._db = db
        self.per_id_calls = []

    def _get_manager(self):
        return self

    def are_embeds_suppressed(self, message_id):
        self.per_id_calls.append(message_id)
        return False


class _Msg:
    id = 101
    author_id = 202
    content = "hello"
    created_at = 1_700_000_000_000
    attachments = []
    embeds = [{"title": "visible only if not suppressed"}]
    metadata = None
    channel_id = 303


@pytest.mark.unit
def test_batch_embed_suppression_uses_one_query_and_no_per_id_calls():
    db = _FakeDB(
        [
            {"message_id": 1, "total": 2, "suppressed_count": 2},
            {"message_id": 2, "total": 2, "suppressed_count": 0},
            {"message_id": 3, "total": 0, "suppressed_count": 0},
        ]
    )
    embeds = _FakeEmbeds(db)

    result = batch_embed_suppressed_ids([1, 2, 3], embeds_mod=embeds)

    assert result == {1}
    assert len(db.calls) == 1
    assert "GROUP BY message_id" in db.calls[0][0]
    assert "OFFSET" not in db.calls[0][0].upper()
    assert embeds.per_id_calls == []


@pytest.mark.unit
def test_batch_embed_suppression_is_bounded_by_chunk_size():
    db = _FakeDB([])
    embeds = _FakeEmbeds(db)
    ids = list(range(1, _MAX_EMBED_SUPPRESSION_CHUNK * 2 + 3))

    batch_embed_suppressed_ids(ids, embeds_mod=embeds)

    assert len(db.calls) == 3
    assert all(len(params) <= _MAX_EMBED_SUPPRESSION_CHUNK for _, params in db.calls)


@pytest.mark.unit
def test_batch_embed_suppression_falls_back_per_id_when_no_batch_db(monkeypatch):
    class _PerIdOnly:
        def __init__(self):
            self.calls = []

        def are_embeds_suppressed(self, message_id):
            self.calls.append(message_id)
            return message_id == 2

    embeds = _PerIdOnly()

    result = batch_embed_suppressed_ids([1, 2, 3], embeds_mod=embeds)

    assert result == {2}
    assert embeds.calls == [1, 2, 3]


@pytest.mark.unit
def test_serializer_accepts_precomputed_suppression_without_embeds_lookup(
    monkeypatch,
):
    def _fail(*args, **kwargs):
        raise AssertionError("serializer must not hit the embeds module per message")

    monkeypatch.setattr(api, "get_embeds", _fail)

    resp = _message_to_response(
        _Msg(), viewer_user_id=None, suppress_embeds=True, media_mod=None
    )

    assert resp.embeds == []
    assert resp.metadata is not None
    assert resp.metadata["embeds_suppressed"] is True
