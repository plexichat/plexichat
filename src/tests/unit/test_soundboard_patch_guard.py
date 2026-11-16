"""Regression tests for the soundboard PATCH cross-server guard.

BEFORE: the PATCH route loaded the full Sound (JOIN + membership probe +
model reconstruction) only for the cross-server guard, then the manager's
``update_sound`` immediately loaded it again. AFTER: the guard reads only
the owning ``server_id`` column; the manager still re-fetches and
re-validates membership/permissions.
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.soundboard import (
    _cheap_require_sound_in_server,
    update_sound,
)
from src.api.schemas.soundboard import SoundUpdateRequest
from src.core.soundboard.models import Sound


class _FakeDB:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def fetch_one(self, sql, params):
        self.calls.append((sql, params))
        return self.row


class _FakeManager:
    def __init__(self, db):
        self._db = db


class _FakeSoundboard:
    def __init__(self, row, sound=None):
        self.db = _FakeDB(row)
        self.sound = sound
        self.get_sound_calls = []
        self.update_calls = []

    def _get_manager(self):
        return _FakeManager(self.db)

    def get_sound(self, sound_id, user_id):
        self.get_sound_calls.append((sound_id, user_id))
        return self.sound

    def update_sound(self, user_id, sound_id, **kwargs):
        self.update_calls.append((user_id, sound_id, kwargs))
        return self.sound


@pytest.mark.unit
def test_guard_reads_only_server_id_without_loading_sound():
    board = _FakeSoundboard(row={"server_id": 555})

    _cheap_require_sound_in_server(board, 10, 555, user_id=7)

    assert len(board.db.calls) == 1
    assert "SELECT server_id" in board.db.calls[0][0]
    assert "usage" not in board.db.calls[0][0].lower()
    assert board.get_sound_calls == []


@pytest.mark.unit
def test_guard_still_rejects_cross_server_sounds():
    board = _FakeSoundboard(row={"server_id": 999})

    with pytest.raises(HTTPException) as exc:
        _cheap_require_sound_in_server(board, 10, 555, user_id=7)

    assert exc.value.status_code == 404
    assert board.get_sound_calls == []


@pytest.mark.unit
def test_guard_falls_back_to_full_load_without_direct_lookup():
    board = _FakeSoundboard(row=None, sound=Sound(id=10, server_id=555, name="s"))

    _cheap_require_sound_in_server(board, 10, 555, user_id=7)

    assert board.get_sound_calls == [(10, 7)]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_patch_route_does_not_reload_sound_for_guard(monkeypatch):
    sound = Sound(id=10, server_id=555, name="updated")
    board = _FakeSoundboard(row={"server_id": 555}, sound=sound)

    def _must_not_call(*args, **kwargs):
        raise AssertionError("PATCH guard must not call get_sound for the server check")

    board.get_sound = _must_not_call
    monkeypatch.setattr(api, "get_soundboard", lambda: board)

    result = await update_sound(
        "555",
        "10",
        SoundUpdateRequest(name="updated"),
        current_user=SimpleNamespace(user_id=7),
    )

    assert result.id == "10"
    assert len(board.db.calls) == 1
    assert len(board.update_calls) == 1
