"""Batched sticker hydration must not aggregate an unused ``usage_count``.

``_batch_pack_stickers`` feeds two callers -- the server sticker-library page
and the ``GUILD_STICKERS_UPDATE`` websocket dispatch -- and both serialize the
hydrated rows with ``StickerResponse``. That schema has no ``usage_count``
field, so the old batch query's ``LEFT JOIN sticker_usage`` +
``COUNT(u.id) as usage_count`` + ``GROUP BY s.id`` scanned every usage row for
every sticker on a hot read/broadcast path for a value nobody consumed.

Regression contract:
* the emitted query is a plain ``sticker_stickers`` scan (no usage JOIN, no
  COUNT, no GROUP BY);
* it still resolves all packs in one query and keeps one row per sticker;
* the resulting rows still hydrate into stickers for every pack.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.api.routes.stickers import _batch_pack_stickers

pytestmark = pytest.mark.unit


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def fetch_all(self, sql, params=()):
        self.calls.append((" ".join(sql.split()), tuple(params)))
        return self.rows


class _FakeManager:
    def __init__(self, db):
        self._db = db

    def _row_to_sticker(self, row):
        # Mirror Sticker._row_to_sticker: a missing usage_count column is a
        # benign 0, which StickerResponse ignores entirely.
        return SimpleNamespace(**row, usage_count=row.get("usage_count", 0))


class _FakeStickers:
    def __init__(self, db):
        self._db = db

    def _get_manager(self):
        return _FakeManager(self._db)


def test_batch_query_has_no_unused_usage_count_aggregation():
    packs = [SimpleNamespace(id=11), SimpleNamespace(id=22)]
    rows = [
        {"pack_id": 11, "id": 1, "name": "a"},
        {"pack_id": 22, "id": 2, "name": "b"},
    ]
    db = _FakeDB(rows)

    result = _batch_pack_stickers(_FakeStickers(db), packs, user_id=7)

    # One batched query for all packs.
    assert len(db.calls) == 1
    sql = db.calls[0][0]

    # The unused per-sticker usage-count aggregation is gone.
    assert "sticker_usage" not in sql
    assert "usage_count" not in sql
    assert "COUNT(" not in sql
    assert "GROUP BY" not in sql
    assert "LEFT JOIN" not in sql

    # The useful shape remains: a single sticker_stickers scan for both packs.
    assert sql.startswith("SELECT s.* FROM sticker_stickers s")
    assert "WHERE s.pack_id IN (" in sql
    assert "ORDER BY s.pack_id, s.name" in sql

    # Hydration still groups every row under its own pack.
    assert [r.id for r in result[11]] == [1]
    assert [r.id for r in result[22]] == [2]
