"""Regression tests for batched sticker-pack hydration.

BEFORE this change ``GET /servers/{id}/stickers`` called
``get_pack_stickers`` once per pack, and each call re-ran ``get_pack``
(JOIN + membership probe) plus its own sticker query. AFTER: one batched
query resolves all packs, with a per-pack fallback preserved.
"""

from types import SimpleNamespace

import pytest

import src.api as api
from src.core.stickers.models import Sticker, StickerPack
from src.api.routes.stickers import (
    _MAX_STICKER_BATCH_CHUNK,
    _batch_pack_stickers,
    _dispatch_stickers_update,
    get_server_stickers,
)


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_all(self, sql, params):
        self.calls.append((sql, params))
        return self.rows


class _FakeManager:
    def __init__(self, db, to_sticker):
        self._db = db
        self._to_sticker = to_sticker

    def _row_to_sticker(self, row):
        return self._to_sticker(row)


class _FakeStickers:
    def __init__(self, db=None, to_sticker=None, packs=None):
        self._db = db
        self._to_sticker = to_sticker or (lambda row: dict(row))
        self.packs = packs or []
        self.per_pack_calls = []

    def _get_manager(self):
        if self._db is None:
            raise RuntimeError("not setup")
        return _FakeManager(self._db, self._to_sticker)

    def get_server_packs(self, user_id, server_id):
        return self.packs

    def get_pack_stickers(self, user_id, pack_id):
        self.per_pack_calls.append(pack_id)
        return []


@pytest.mark.unit
def test_batch_pack_stickers_uses_one_query_and_no_per_pack_calls():
    packs = [SimpleNamespace(id=pid) for pid in (11, 22, 33)]
    rows = [
        {"pack_id": 11, "id": 1, "name": "a"},
        {"pack_id": 11, "id": 2, "name": "b"},
        {"pack_id": 33, "id": 3, "name": "c"},
    ]
    db = _FakeDB(rows)
    stickers = _FakeStickers(db=db)

    result = _batch_pack_stickers(stickers, packs, user_id=7)

    assert set(result) == {11, 22, 33}
    assert [s["id"] for s in result[11]] == [1, 2]
    assert result[22] == []
    assert [s["id"] for s in result[33]] == [3]
    assert len(db.calls) == 1
    assert "sticker_stickers" in db.calls[0][0]
    # usage_count is not part of StickerResponse: no unused aggregation.
    assert "sticker_usage" not in db.calls[0][0]
    assert "COUNT(" not in db.calls[0][0]
    assert "GROUP BY" not in db.calls[0][0]
    assert stickers.per_pack_calls == []


@pytest.mark.unit
def test_batch_pack_stickers_is_bounded_by_chunk_size():
    packs = [
        SimpleNamespace(id=pid) for pid in range(1, _MAX_STICKER_BATCH_CHUNK * 2 + 3)
    ]
    db = _FakeDB([])
    stickers = _FakeStickers(db=db)

    _batch_pack_stickers(stickers, packs, user_id=7)

    assert len(db.calls) == 3
    assert all(len(params) <= _MAX_STICKER_BATCH_CHUNK for _, params in db.calls)


@pytest.mark.unit
def test_batch_pack_stickers_falls_back_per_pack_without_batch_db():
    packs = [SimpleNamespace(id=pid) for pid in (1, 2)]
    stickers = _FakeStickers(db=None)

    result = _batch_pack_stickers(stickers, packs, user_id=7)

    assert result == {1: [], 2: []}
    assert stickers.per_pack_calls == [1, 2]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_server_stickers_hydrates_packs_from_one_query(monkeypatch):
    packs = [
        StickerPack(id=1, name="pack one", pack_type="server", server_id=555),  # pyright: ignore[reportArgumentType]
        StickerPack(id=2, name="pack two", pack_type="server", server_id=555),  # pyright: ignore[reportArgumentType]
    ]
    rows = [
        {
            "pack_id": 1,
            "id": 101,
            "name": "sticker a",
            "format": "png",
            "url": "https://example.test/a.png",
            "size": 10,
            "tags": [],
        },
        {
            "pack_id": 2,
            "id": 102,
            "name": "sticker b",
            "format": "png",
            "url": "https://example.test/b.png",
            "size": 20,
            "tags": [],
        },
    ]
    db = _FakeDB(rows)
    stickers = _FakeStickers(db=db, packs=packs, to_sticker=lambda row: Sticker(**row))
    monkeypatch.setattr(api, "get_stickers", lambda: stickers)

    # get_server_stickers runs a fail-closed live membership gate before the
    # cached loader. Wire a servers module that admits user 7 so the route
    # reaches the batching path under test (and prove the gate was consulted).
    member_checks = []

    def _get_member(server_id, user_id):
        member_checks.append((server_id, user_id))
        return SimpleNamespace(user_id=user_id, server_id=server_id)

    monkeypatch.setattr(
        api, "get_servers", lambda: SimpleNamespace(get_member=_get_member)
    )

    result = await get_server_stickers("555", current_user=SimpleNamespace(user_id=7))  # pyright: ignore[reportArgumentType]

    assert [p.id for p in result] == ["1", "2"]
    assert [s.name for s in result[0].stickers] == ["sticker a"]
    assert [s.name for s in result[1].stickers] == ["sticker b"]
    assert len(db.calls) == 1
    assert stickers.per_pack_calls == []
    assert member_checks == [(555, 7)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_dispatch_update_batches_sticker_lookups(monkeypatch):
    packs = [
        StickerPack(id=1, name="pack one", pack_type="server", server_id=555),  # pyright: ignore[reportArgumentType]
        StickerPack(id=2, name="pack two", pack_type="server", server_id=555),  # pyright: ignore[reportArgumentType]
    ]
    rows = [
        {
            "pack_id": 1,
            "id": 101,
            "name": "a",
            "format": "png",
            "url": "https://example.test/a.png",
            "size": 1,
            "tags": [],
        },
        {
            "pack_id": 2,
            "id": 102,
            "name": "b",
            "format": "png",
            "url": "https://example.test/b.png",
            "size": 2,
            "tags": [],
        },
    ]
    db = _FakeDB(rows)
    stickers = _FakeStickers(db=db, packs=packs, to_sticker=lambda row: Sticker(**row))
    monkeypatch.setattr(api, "get_stickers", lambda: stickers)
    monkeypatch.setattr(
        api, "get_servers", lambda: SimpleNamespace(get_member_user_ids=lambda *_: [7])
    )

    class _Dispatcher:
        def __init__(self):
            self.events = []

        async def dispatch_event(self, event, user_ids):
            self.events.append((event, user_ids))

    dispatcher = _Dispatcher()
    monkeypatch.setattr("src.api.websocket.get_dispatcher", lambda: dispatcher)
    monkeypatch.setattr("src.api.websocket.is_setup", lambda: True)

    await _dispatch_stickers_update(555, 7)

    assert len(dispatcher.events) == 1
    sent_packs = dispatcher.events[0][0].data["sticker_packs"]
    assert [[s["name"] for s in p["stickers"]] for p in sent_packs] == [["a"], ["b"]]
    assert len(db.calls) == 1
    assert stickers.per_pack_calls == []
