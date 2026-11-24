"""Regressions for the hot server-library and artifact read surfaces.

The four endpoints in this sweep (server stickers, server soundboard, artifact
list, artifact get) must stay bounded, scoped and cheap. These tests pin the
new guarantees with counting fakes so a future refactor cannot silently
reintroduce an unbounded response, a cross-server (IDOR) row, a cached denial,
or an artifact-type capability bypass.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes.artifacts.routes import crud as artifacts_crud
from src.api.routes.soundboard import (
    _load_server_sounds_page,
    list_server_sounds,
)
from src.api.routes.stickers import (
    _load_server_stickers_page,
    get_server_stickers,
)
from src.api.schemas.soundboard import SoundResponse
from src.api.schemas.stickers import StickerPackResponse
from src.core.database import invalidate_pattern
from src.core.soundboard.exceptions import (
    PermissionDeniedError as SoundPermissionDeniedError,
)
from src.core.soundboard.models import Sound
from src.core.stickers.exceptions import PermissionDeniedError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

_USER_ID = 42
_SERVER_ID = 555


def _as_dict(value):
    """Cold path returns models, warm cache hit returns dicts; compare shapes."""
    return value if isinstance(value, dict) else value.model_dump()


@pytest.fixture(autouse=True)
def _clear_hot_caches():
    invalidate_pattern("server_stickers_api:*")
    invalidate_pattern("server_sounds_api:*")
    yield
    invalidate_pattern("server_stickers_api:*")
    invalidate_pattern("server_sounds_api:*")


# --- fakes -----------------------------------------------------------------


class _FakeDB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def fetch_all(self, sql, params=None):
        self.calls += 1
        return list(self.rows)


class _FakeManager:
    def __init__(self, db, to_row=None):
        self._db = db
        self._to_row = to_row or (lambda row: SimpleNamespace(**row))

    def _row_to_sticker(self, row):
        return self._to_row(row)

    def _row_to_sound(self, row):
        return self._to_row(row)


def _sticker_row(pack_id: int, index: int) -> dict:
    return {
        "pack_id": pack_id,
        "id": 1000 + pack_id * 10 + index,
        "name": f"sticker-{index}",
        "format": "png",
        "url": f"https://cdn.test/{pack_id}-{index}.png",
        "size": 10,
        "tags": [],
    }


def _pack(pack_id: int, server_id: int):
    return SimpleNamespace(
        id=pack_id,
        name=f"pack-{pack_id}",
        pack_type="server",
        server_id=server_id,
        sticker_count=2,
    )


def _sound(index: int, server_id: int) -> Sound:
    return Sound(
        id=2000 + index,
        server_id=server_id,
        name=f"sound-{index}",
        url=f"https://cdn.test/{index}.mp3",
        size=10,
        duration_seconds=1.0,
        created_by=_USER_ID,
        created_at=1,
    )


# --- stickers ---------------------------------------------------------------


@pytest.fixture
def sticker_module(monkeypatch):
    db = _FakeDB([_sticker_row(1, i) for i in range(2)])
    manager = _FakeManager(db)
    packs = [_pack(1, _SERVER_ID), _pack(2, _SERVER_ID)]

    class _Stickers:
        calls = 0

        def _get_manager(self):
            return manager

        def get_server_packs(self, user_id, server_id):
            type(self).calls += 1
            return list(packs)

    module = _Stickers()
    monkeypatch.setattr(api, "get_stickers", lambda: module)
    return module, db


async def test_sticker_page_is_bounded_and_cached_identically(sticker_module):
    module, db = sticker_module

    first = await _load_server_stickers_page(_SERVER_ID, _USER_ID, 1, 0)
    assert len(first) == 1
    assert isinstance(first[0], StickerPackResponse)
    cold_db_calls = db.calls
    cold_manager_calls = module.calls

    # A warm read must serve the identical payload without touching the DB or
    # re-running the manager (that is the whole point of the memoized page).
    warm = await _load_server_stickers_page(_SERVER_ID, _USER_ID, 1, 0)
    assert [_as_dict(p) for p in warm] == [_as_dict(p) for p in first]
    assert db.calls == cold_db_calls
    assert module.calls == cold_manager_calls

    # Distinct pages stay distinct (no cache-key collision across offsets).
    second = await _load_server_stickers_page(_SERVER_ID, _USER_ID, 1, 1)
    assert [p.id for p in second] != [p.id for p in first]


async def test_sticker_page_drops_cross_server_packs(monkeypatch):
    """IDOR: a pack from another server must never be returned."""
    db = _FakeDB([])
    module = SimpleNamespace(
        _get_manager=lambda: _FakeManager(db),
        get_server_packs=lambda user_id, server_id: [
            _pack(1, _SERVER_ID),
            _pack(2, _SERVER_ID + 1),
        ],
    )
    monkeypatch.setattr(api, "get_stickers", lambda: module)

    result = await _load_server_stickers_page(_SERVER_ID, _USER_ID, 200, 0)

    assert [p.id for p in result] == ["1"]


async def test_non_member_denial_is_never_cached(monkeypatch):
    """A 403 must stay live; no memoized response may survive a denial."""
    gate = {"n": 0}
    manager = {"n": 0}

    class _Denied:
        def get_server_packs(self, user_id, server_id):
            manager["n"] += 1
            raise PermissionDeniedError("not a member")

    class _Servers:
        def get_member(self, server_id, user_id):
            gate["n"] += 1
            return None  # not a member: the live gate denies

    monkeypatch.setattr(api, "get_stickers", lambda: _Denied())
    monkeypatch.setattr(api, "get_servers", lambda: _Servers())
    user = SimpleNamespace(user_id=_USER_ID)

    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            await get_server_stickers(str(_SERVER_ID), current_user=user)
        assert exc.value.status_code == 403

    # The membership gate re-runs on every request -- a denial is never cached
    # -- and the cold library path is never entered for a non-member.
    assert gate["n"] == 2
    assert manager["n"] == 0


# --- soundboard -------------------------------------------------------------


@pytest.fixture
def soundboard_module(monkeypatch):
    db = _FakeDB([])
    manager = _FakeManager(db, to_row=lambda row: Sound(**row))
    sounds = [_sound(i, _SERVER_ID) for i in range(4)]

    class _Soundboard:
        calls = 0

        def _get_manager(self):
            return manager

        def get_server_sounds(self, user_id, server_id):
            type(self).calls += 1
            return list(sounds)

    module = _Soundboard()
    monkeypatch.setattr(api, "get_soundboard", lambda: module)
    return module, db


async def test_soundboard_page_is_bounded_and_cached_identically(soundboard_module):
    module, db = soundboard_module

    first = await _load_server_sounds_page(_SERVER_ID, _USER_ID, 2, 0)
    assert len(first) == 2
    assert isinstance(first[0], SoundResponse)
    cold_db_calls, cold_calls = db.calls, module.calls

    warm = await _load_server_sounds_page(_SERVER_ID, _USER_ID, 2, 0)
    assert [_as_dict(s) for s in warm] == [_as_dict(s) for s in first]
    assert db.calls == cold_db_calls
    assert module.calls == cold_calls

    second = await _load_server_sounds_page(_SERVER_ID, _USER_ID, 2, 2)
    assert {s.id for s in second}.isdisjoint({s.id for s in first})


async def test_soundboard_page_drops_cross_server_sounds(monkeypatch):
    db = _FakeDB([])
    module = SimpleNamespace(
        _get_manager=lambda: _FakeManager(db),
        get_server_sounds=lambda user_id, server_id: [
            _sound(1, _SERVER_ID),
            _sound(2, _SERVER_ID + 1),
        ],
    )
    monkeypatch.setattr(api, "get_soundboard", lambda: module)

    result = await _load_server_sounds_page(_SERVER_ID, _USER_ID, 200, 0)

    assert [s.id for s in result] == ["2001"]


async def test_soundboard_non_member_denial_is_never_cached(monkeypatch):
    gate = {"n": 0}
    manager = {"n": 0}

    class _Denied:
        def _get_manager(self):
            return SimpleNamespace()

        def get_server_sounds(self, user_id, server_id):
            manager["n"] += 1
            raise SoundPermissionDeniedError("not a member")

    class _Servers:
        def get_member(self, server_id, user_id):
            gate["n"] += 1
            return None  # not a member: the live gate denies

    monkeypatch.setattr(api, "get_soundboard", lambda: _Denied())
    monkeypatch.setattr(api, "get_servers", lambda: _Servers())
    user = SimpleNamespace(user_id=_USER_ID)

    for _ in range(2):
        with pytest.raises(HTTPException) as exc:
            await list_server_sounds(str(_SERVER_ID), current_user=user)
        assert exc.value.status_code == 403

    assert gate["n"] == 2
    assert manager["n"] == 0


# --- artifact capability scope --------------------------------------------


async def test_artifact_type_filter_enforces_its_capability(monkeypatch):
    """A disabled per-type capability must not be bypassable by the list filter.

    The unfiltered list checks only the base `artifacts` capability; the
    single-artifact GET checks the actual type. Filtering by a disabled type
    used to list those artifacts anyway.
    """
    from src.core.artifacts.models import ArtifactType

    seen = []

    def _gate(artifact_type):
        seen.append(artifact_type)
        if artifact_type != ArtifactType.UPLOAD:
            raise HTTPException(status_code=403, detail="capability disabled")
        return "artifacts"

    monkeypatch.setattr(artifacts_crud, "_require_artifact_capability", _gate)
    monkeypatch.setattr(artifacts_crud, "_get_manager", lambda: SimpleNamespace())
    monkeypatch.setattr(api, "get_servers", lambda: None)
    monkeypatch.setattr(api, "get_messaging", lambda: None)
    monkeypatch.setattr(api, "get_db", lambda: None)
    user = SimpleNamespace(user_id=_USER_ID)

    with pytest.raises(HTTPException) as exc:
        await artifacts_crud.list_artifacts(
            artifact_type="whiteboard", current_user=user
        )
    assert exc.value.status_code == 403
    assert ArtifactType.WHITEBOARD in seen
