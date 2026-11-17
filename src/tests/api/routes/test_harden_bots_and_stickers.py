"""Regression tests for two HTTP-boundary weaknesses in the bot/sticker routes.

1. ``GET /bots/servers/{id}/approved`` and ``GET /bots/servers/{id}/requests``
   were readable by any authenticated user. The core lookups are keyed only by
   ``server_id`` and apply no authorization, so the queue (requester, reason,
   reviewer notes) of *any* server was enumerable platform-wide. Both routes
   now gate on ``server.manage`` and fail closed.

2. ``POST /servers/{id}/stickers`` used a bare ``await image.read()``, which
   materialised the whole multipart part before checking the size limit. A
   single oversized upload (or one with a missing/lying Content-Length) could
   exhaust the worker. The read now goes through the capped helper.
"""

import pytest
from fastapi import HTTPException

import src.api as api_module
import src.api.routes.bots as bots_routes
import src.api.routes.stickers as stickers_routes
from src.api.routes.bots import list_approved_bots, list_bot_requests
from src.api.routes.stickers import STICKER_MAX_UPLOAD_BYTES, create_sticker
from src.core.stickers import PackType


class _User:
    user_id = 7


class _Servers:
    def __init__(self, allowed=False, raises=False):
        self.allowed = allowed
        self.raises = raises
        self.calls = []

    def has_permission(self, user_id, server_id, permission):
        self.calls.append((user_id, server_id, permission))
        if self.raises:
            raise RuntimeError("permission backend unavailable")
        return self.allowed


class _BotRequest:
    id = 1
    server_id = 10
    application_id = 20
    requester_id = 30
    reason = "please approve"
    status = "pending"
    reviewed_by = None
    review_reason = "internal moderator note"
    created_at = 100


class _ApprovedBot:
    id = 2
    server_id = 10
    application_id = 20
    approved_by = 40
    permissions = "{}"
    bot_name = "Test Bot"
    bot_avatar_url = None
    status = "approved"
    installed_at = 100


@pytest.fixture
def mock_bot_core(monkeypatch):
    """Install a servers module and record core lookup calls."""
    calls = {"requests": [], "approved": []}

    def _install(servers):
        monkeypatch.setattr(api_module, "get_servers", lambda: servers)
        monkeypatch.setattr(
            bots_routes.applications,
            "get_bot_requests",
            lambda **kwargs: (calls["requests"].append(kwargs), [_BotRequest()])[1],
        )
        monkeypatch.setattr(
            bots_routes.applications,
            "get_approved_bots",
            lambda **kwargs: (calls["approved"].append(kwargs), [_ApprovedBot()])[1],
        )
        return calls

    return _install


class TestServerScopedBotListingAuthorization:
    @pytest.mark.asyncio
    async def test_bot_requests_denied_without_manage(self, mock_bot_core):
        calls = mock_bot_core(_Servers(allowed=False))
        with pytest.raises(HTTPException) as exc:
            await list_bot_requests(server_id="10", current_user=_User())
        assert exc.value.status_code == 403
        assert calls["requests"] == [], (
            "core lookup must not run for an unauthorized caller"
        )

    @pytest.mark.asyncio
    async def test_approved_bots_denied_without_manage(self, mock_bot_core):
        calls = mock_bot_core(_Servers(allowed=False))
        with pytest.raises(HTTPException) as exc:
            await list_approved_bots(server_id="10", current_user=_User())
        assert exc.value.status_code == 403
        assert calls["approved"] == []

    @pytest.mark.asyncio
    async def test_denied_when_servers_module_unavailable(self, mock_bot_core):
        mock_bot_core(None)
        with pytest.raises(HTTPException) as exc:
            await list_bot_requests(server_id="10", current_user=_User())
        assert exc.value.status_code == 503

    @pytest.mark.asyncio
    async def test_permission_lookup_failure_denies_closed(self, mock_bot_core):
        calls = mock_bot_core(_Servers(raises=True))
        with pytest.raises(HTTPException) as exc:
            await list_bot_requests(server_id="10", current_user=_User())
        assert exc.value.status_code == 403
        assert calls["requests"] == []

    @pytest.mark.asyncio
    async def test_bot_requests_allowed_for_manager(self, mock_bot_core):
        calls = mock_bot_core(_Servers(allowed=True))
        result = await list_bot_requests(server_id="10", current_user=_User())
        assert len(result) == 1
        assert result[0].reason == "please approve"
        assert result[0].review_reason == "internal moderator note"
        assert calls["requests"] == [{"server_id": 10, "status": None}]

    @pytest.mark.asyncio
    async def test_different_server_still_requires_its_own_permission(
        self, mock_bot_core
    ):
        servers = _Servers(allowed=True)
        mock_bot_core(servers)
        await list_bot_requests(server_id="999", current_user=_User())
        assert servers.calls == [(7, 999, "server.manage")]


class _HugeUpload:
    """Multipart part with no trustworthy declared size and an unbounded read."""

    def __init__(self, total):
        self.size = None
        self.content_type = "image/png"
        self.filename = "big.png"
        self._data = b"\x89PNG\r\n\x1a\n" + b"A" * total
        self.read_calls = []

    async def read(self, n=-1):
        self.read_calls.append(n)
        if n is None or n < 0:
            return self._data
        return self._data[:n]


class _Pack:
    id = 1
    pack_type = PackType.SERVER


class _StubStickers:
    def __init__(self, sticker=None):
        self._sticker = sticker
        self.pack_creations = 0
        self.create_calls = 0

    def get_server_packs(self, user_id, sid):
        return [_Pack()]

    def create_pack(self, **kwargs):
        self.pack_creations += 1
        return _Pack()

    def create_sticker_from_file(self, **kwargs):
        self.create_calls += 1
        return self._sticker


@pytest.fixture
def mock_sticker_core(monkeypatch):
    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(stickers_routes, "_check_sticker_create_rate_limit", _noop)
    monkeypatch.setattr(stickers_routes, "_dispatch_stickers_update", _noop)

    def _install(stub):
        monkeypatch.setattr(api_module, "get_stickers", lambda: stub)
        return stub

    return _install


class TestStickerUploadIsCapped:
    @pytest.mark.asyncio
    async def test_oversized_upload_rejected_with_bounded_read(self, mock_sticker_core):
        stub = mock_sticker_core(_StubStickers())
        upload = _HugeUpload(STICKER_MAX_UPLOAD_BYTES * 3)

        with pytest.raises(HTTPException) as exc:
            await create_sticker(
                server_id="10",
                name="validname",
                image=upload,
                tags=None,
                description=None,
                current_user=_User(),
            )

        assert exc.value.status_code == 413
        # The decisive assertion: the route must never call read() unbounded.
        assert upload.read_calls, "file was not read"
        assert all(n is not None and n >= 0 for n in upload.read_calls), (
            f"unbounded read() call found: {upload.read_calls}"
        )
        assert max(upload.read_calls) <= STICKER_MAX_UPLOAD_BYTES + 1
        # Rejection happens before any write side effect.
        assert stub.pack_creations == 0
        assert stub.create_calls == 0

    @pytest.mark.asyncio
    async def test_lying_declared_size_does_not_bypass_cap(self, mock_sticker_core):
        stub = mock_sticker_core(_StubStickers())
        upload = _HugeUpload(STICKER_MAX_UPLOAD_BYTES + 64)
        upload.size = 1  # under-report the payload

        with pytest.raises(HTTPException) as exc:
            await create_sticker(
                server_id="10",
                name="validname",
                image=upload,
                tags=None,
                description=None,
                current_user=_User(),
            )

        assert exc.value.status_code == 413
        assert max(upload.read_calls) <= STICKER_MAX_UPLOAD_BYTES + 1
        assert stub.create_calls == 0

    @pytest.mark.asyncio
    async def test_normal_upload_still_succeeds(self, mock_sticker_core):
        sticker = {
            "id": 5,
            "pack_id": 1,
            "name": "validname",
            "format": "png",
            "url": "https://example.test/s.png",
            "size": 8,
        }
        stub = mock_sticker_core(_StubStickers(sticker))
        upload = _HugeUpload(STICKER_MAX_UPLOAD_BYTES // 2)

        result = await create_sticker(
            server_id="10",
            name="validname",
            image=upload,
            tags=None,
            description=None,
            current_user=_User(),
        )

        assert result.id == "5"
        assert stub.create_calls == 1
        assert max(upload.read_calls) <= STICKER_MAX_UPLOAD_BYTES + 1
