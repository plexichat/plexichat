"""Regression coverage for batched settings resolution in the voice channel list path.

``get_voice_channels`` used to fire one ``voice_channel_settings`` SELECT per
visible channel (N+1). It now resolves settings in bounded ``IN`` chunks.
These tests pin three properties of that change:

* **Result parity** - the batched path returns exactly the same
  ``VoiceChannel`` objects as the old per-id path, including the default
  settings synthesised for channels with no row.
* **Query-count reduction** - a list of N channels emits a constant number
  of settings SELECTs instead of N.
* **Bounds** - no single ``IN`` clause binds more than 500 ids, and the
  fail-closed authorization pre-filter still scopes the batched lookups to
  channels the caller may view.
"""

import math
from dataclasses import asdict

from src.core.voice.manager.base import _BULK_ID_CHUNK_SIZE


SERVER_ID = 10
USER_ID = 1


class _FakeDB:
    """Minimal DB double that records every query and its bind parameters."""

    type = "sqlite"

    def __init__(self, channels, settings, counts=None):
        self.channels = channels
        self.settings = settings
        self.counts = counts or {}
        self.fetch_all_calls = []
        self.fetch_one_calls = []

    def fetch_all(self, sql, params):
        params = tuple(params)
        self.fetch_all_calls.append((sql, params))
        if "FROM srv_channels" in sql:
            return [dict(c) for c in self.channels if c["server_id"] == params[0]]
        if "FROM voice_states" in sql:
            return [
                {"channel_id": cid, "count": self.counts[cid]}
                for cid in params
                if cid in self.counts
            ]
        if "FROM voice_channel_settings" in sql:
            return [
                dict(self.settings[cid]) for cid in params if cid in self.settings
            ]
        raise AssertionError(f"unexpected fetch_all SQL: {sql}")

    def fetch_one(self, sql, params):
        self.fetch_one_calls.append((sql, tuple(params)))
        if "FROM voice_channel_settings" in sql:
            row = self.settings.get(params[0])
            return dict(row) if row else None
        raise AssertionError(f"unexpected fetch_one SQL: {sql}")


class _FakeServers:
    """Permission source that denies only explicitly listed channels."""

    def __init__(self, deny_ids=()):
        self.deny_ids = set(deny_ids)

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        return channel_id not in self.deny_ids


def _make_channel(channel_id, kind="voice"):
    return {
        "id": channel_id,
        "server_id": SERVER_ID,
        "name": f"chan-{channel_id}",
        "channel_type": kind,
        "position": channel_id,
        "category_id": None,
        "created_at": 0,
        "updated_at": 0,
        "deleted": 0,
    }


def _make_manager(channels, settings, deny_ids=(), counts=None):
    from src.core.voice.manager import VoiceManager

    db = _FakeDB(channels, settings, counts)
    manager = object.__new__(VoiceManager)
    manager._db = db
    manager._servers = _FakeServers(deny_ids)
    return manager, db


def _settings_queries(db):
    return [c for c in db.fetch_all_calls if "FROM voice_channel_settings" in c[0]]


def _count_queries(db):
    return [c for c in db.fetch_all_calls if "FROM voice_states" in c[0]]


def _settings_single_queries(db):
    return [c for c in db.fetch_one_calls if "FROM voice_channel_settings" in c[0]]


def test_batched_settings_preserve_results_and_cut_queries():
    """Batched lookup is output-identical to the old per-id path, with fewer queries."""
    channels = [_make_channel(100 + i, "stage" if i % 2 else "voice") for i in range(7)]
    # Only even ids have a persisted row; odd ids exercise the default-dict path.
    settings = {
        c["id"]: {
            "channel_id": c["id"],
            "user_limit": 25,
            "bitrate": 96000,
            "region_id": "eu",
        }
        for c in channels
        if c["id"] % 2 == 0
    }
    manager, db = _make_manager(channels, settings)

    # --- New batched path ---
    db.fetch_all_calls.clear()
    db.fetch_one_calls.clear()
    batched = manager.get_voice_channels(USER_ID, SERVER_ID)
    assert len(batched) == len(channels)
    batched_settings_selects = len(_settings_queries(db))
    batched_settings_singles = len(_settings_single_queries(db))

    # --- Reference implementation: the old per-channel fetch ---
    real_bulk = manager._get_channel_settings_bulk

    def _reference_bulk(channel_ids):
        return {cid: manager._get_channel_settings(cid) for cid in channel_ids}

    manager._get_channel_settings_bulk = _reference_bulk
    try:
        db.fetch_all_calls.clear()
        db.fetch_one_calls.clear()
        reference = manager.get_voice_channels(USER_ID, SERVER_ID)
        reference_settings_singles = len(_settings_single_queries(db))
    finally:
        manager._get_channel_settings_bulk = real_bulk

    # Result parity, including synthesized defaults and clamped values.
    assert [asdict(c) for c in batched] == [asdict(c) for c in reference]

    # Query-count reduction: 1 bulk SELECT instead of N single SELECTs.
    assert batched_settings_selects == 1
    assert batched_settings_singles == 0
    assert reference_settings_singles == len(channels)


def test_batched_settings_and_counts_respect_bind_bounds():
    """Large channel lists chunk both bulk queries into <=500-id IN binds."""
    total = (2 * _BULK_ID_CHUNK_SIZE) + 3  # 1003 channels -> 3 chunks
    channels = [_make_channel(1000 + i) for i in range(total)]
    settings = {
        cid: {"channel_id": cid, "user_limit": 10, "bitrate": 48000, "region_id": None}
        for cid in (c["id"] for c in channels)
        if (cid - 1000) % 3 == 0
    }
    counts = {channels[0]["id"]: 4, channels[5]["id"]: 9}
    manager, db = _make_manager(channels, settings, counts=counts)

    result = manager.get_voice_channels(USER_ID, SERVER_ID)
    assert len(result) == total

    expected_chunks = math.ceil(total / _BULK_ID_CHUNK_SIZE)
    settings_queries = _settings_queries(db)
    count_queries = _count_queries(db)
    assert len(settings_queries) == expected_chunks
    assert len(count_queries) == expected_chunks

    bound_ids = []
    for _sql, params in settings_queries:
        assert 1 <= len(params) <= _BULK_ID_CHUNK_SIZE
        bound_ids.extend(params)
    assert len(bound_ids) == total
    assert len(set(bound_ids)) == total

    for _sql, params in count_queries:
        assert 1 <= len(params) <= _BULK_ID_CHUNK_SIZE

    # Non-zero counts and default settings survive the batching.
    by_id = {c.id: c for c in result}
    assert by_id[channels[0]["id"]].user_count == 4
    assert by_id[channels[5]["id"]].user_count == 9
    assert by_id[channels[1]["id"]].user_limit == 0
    assert by_id[channels[1]["id"]].bitrate == 64000


def test_batched_settings_scoped_to_authorized_channels_and_fails_closed():
    """The authz pre-filter still bounds which ids reach the batched lookup."""
    channels = [_make_channel(2000 + i) for i in range(6)]
    settings = {
        c["id"]: {
            "channel_id": c["id"],
            "user_limit": 0,
            "bitrate": 64000,
            "region_id": None,
        }
        for c in channels
    }
    denied = {channels[1]["id"], channels[4]["id"]}
    manager, db = _make_manager(channels, settings, deny_ids=denied)

    result = manager.get_voice_channels(USER_ID, SERVER_ID)
    assert len(result) == len(channels) - len(denied)
    bound_ids = [cid for _sql, params in _settings_queries(db) for cid in params]
    assert set(bound_ids).isdisjoint(denied)
    assert len(bound_ids) == len(result)

    # No servers module -> nothing is authorized; no lookups are issued.
    manager._servers = None
    db.fetch_all_calls.clear()
    assert manager.get_voice_channels(USER_ID, SERVER_ID) == []
    assert _settings_queries(db) == []
    assert _count_queries(db) == []
