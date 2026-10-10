"""Regression tests: sticker route robustness (Agent-31 LOW leftovers).

- M1: ``GET /stickers/suggestions`` parses ``server_id`` inside its try so a
  non-numeric value answers 400 instead of an unhandled ValueError -> 500.
- M2: a concurrent delete between ``get_sticker`` and ``remove_sticker``
  surfaces as 404 via StickerNotFoundError, never the generic 500.
- M3: ``POST /stickers/{id}/send`` is rate limited per user (previously a
  free unlimited DB-write amplification vector).
"""

import pytest

import src.api as api_module
from src.api.routes.stickers import (
    delete_sticker,
    get_sticker_suggestions,
    send_sticker_usage,
)
from src.core.stickers.exceptions import StickerNotFoundError
from fastapi import HTTPException

from src.core import ratelimit


class _User:
    def __init__(self, user_id=77):
        self.user_id = user_id


class _RLResult:
    def __init__(self, allowed, retry_after=9):
        self.allowed = allowed
        self.retry_after = retry_after


class _StubStickers:
    """Minimal sticker-manager stand-in for the route layer."""

    def __init__(self, remove_raises=None, sticker=None, pack=None):
        self._remove_raises = remove_raises
        self._sticker = sticker
        self._pack = pack
        self.sent = []

    def get_sticker(self, sid):
        return self._sticker

    def get_pack(self, pack_id, user_id):
        return self._pack

    def remove_sticker(self, user_id, sid):
        if self._remove_raises is not None:
            raise self._remove_raises

    def can_use_sticker(self, user_id, sid):
        return True

    def send_sticker(self, user_id, mid, sid):
        self.sent.append((user_id, mid, sid))

    def get_sticker_suggestions(self, user_id, content, sid, limit):
        return []


@pytest.fixture
def stub_api(monkeypatch):
    def _install(stub):
        monkeypatch.setattr(api_module, "get_stickers", lambda: stub)
        return stub

    return _install


class TestSuggestionsServerIdParsing:
    @pytest.mark.asyncio
    async def test_non_numeric_server_id_answers_400(self, stub_api):
        """M1: 'abc' must 400, not raise ValueError -> 500."""
        stub_api(_StubStickers())
        with pytest.raises(HTTPException) as excinfo:
            await get_sticker_suggestions(
                content="hello", server_id="abc", limit=5, current_user=_User()
            )
        assert excinfo.value.status_code == 400

    @pytest.mark.asyncio
    async def test_numeric_server_id_passes_through(self, stub_api):
        stub_api(_StubStickers())
        result = await get_sticker_suggestions(
            content="hello", server_id="123", limit=5, current_user=_User()
        )
        assert result == []


class TestDeleteRaceMapping:
    @pytest.mark.asyncio
    async def test_concurrent_delete_maps_to_404(self, stub_api, monkeypatch):
        """M2: StickerNotFoundError from remove_sticker -> 404, not 500."""

        class _Sticker:
            pack_id = 555

        class _Pack:
            server_id = None  # skip dispatch path

        stub = _StubStickers(
            remove_raises=StickerNotFoundError("gone"),
            sticker=_Sticker(),
            pack=_Pack(),
        )
        stub_api(stub)

        with pytest.raises(HTTPException) as excinfo:
            await delete_sticker(sticker_id="42", current_user=_User())
        assert excinfo.value.status_code == 404


class TestCreateRateLimitAtomic:
    """L3: the sticker-create limiter must be a single atomic increment.

    The old implementation read the counter (increment 0), compared it
    against the budget, then incremented (+1) — two separate storage calls
    that let concurrent requests both read the same sub-limit value and
    both pass. increment_custom_usage is atomic under the storage lock, so
    the check and the charge must happen in ONE call.
    """

    def _install_ratelimit(self, monkeypatch, manager):
        monkeypatch.setattr(ratelimit, "is_setup", lambda: True)
        monkeypatch.setattr(ratelimit, "get_manager", lambda: manager)

    @pytest.mark.asyncio
    async def test_over_budget_rejects_with_single_increment(self, monkeypatch):
        """The (max+1)th request in the window is rejected."""
        from src.api.routes.stickers import _check_sticker_create_rate_limit

        calls = []

        class _FakeManager:
            async def aincrement_custom_usage(self, key, field, amount, ttl=86400):
                calls.append((key, field, amount, ttl))
                return 21  # one past the 20/min budget

        self._install_ratelimit(monkeypatch, _FakeManager())

        with pytest.raises(HTTPException) as excinfo:
            await _check_sticker_create_rate_limit(user_id=7)
        assert excinfo.value.status_code == 429
        # Atomic: exactly one +1 increment, never a separate read call.
        assert len(calls) == 1
        assert calls[0][2] == 1

    @pytest.mark.asyncio
    async def test_at_budget_passes_with_single_increment(self, monkeypatch):
        """Requests 1..max pass; only the (max+1)th is cut off."""
        from src.api.routes.stickers import _check_sticker_create_rate_limit

        calls = []

        class _FakeManager:
            async def aincrement_custom_usage(self, key, field, amount, ttl=86400):
                calls.append((key, field, amount, ttl))
                return 20  # exactly at budget

        self._install_ratelimit(monkeypatch, _FakeManager())

        await _check_sticker_create_rate_limit(user_id=7)
        assert len(calls) == 1
        assert calls[0][2] == 1


class TestUsageRateLimit:
    @pytest.mark.asyncio
    async def test_send_usage_429_when_limited(self, stub_api, monkeypatch):
        """M3: a disallowed rate-limit result must 429 with Retry-After."""
        stub_api(_StubStickers())

        async def _deny(**kwargs):
            return _RLResult(allowed=False, retry_after=9)

        monkeypatch.setattr(ratelimit, "acheck_rate_limit", _deny)

        with pytest.raises(HTTPException) as excinfo:
            await send_sticker_usage(
                sticker_id="42", message_id="99", request=None, current_user=_User()
            )
        assert excinfo.value.status_code == 429
        assert excinfo.value.headers.get("Retry-After") == "9"

    @pytest.mark.asyncio
    async def test_send_usage_records_when_allowed(self, stub_api, monkeypatch):
        stub = _StubStickers()
        stub_api(stub)

        async def _allow(**kwargs):
            return _RLResult(allowed=True, retry_after=0)

        monkeypatch.setattr(ratelimit, "acheck_rate_limit", _allow)

        result = await send_sticker_usage(
            sticker_id="42", message_id="99", request=None, current_user=_User(7)
        )
        assert result == {"success": True}
        assert stub.sent == [(7, 99, 42)]
