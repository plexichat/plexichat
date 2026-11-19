"""Sticker route IDs must use the canonical ASCII/bounded snowflake gate.

The sticker routes used to parse client-controlled path/query/form ids with a
bare ``int()``. ``int()`` accepts Unicode decimal homoglyphs and PEP 515
separators that every other hop renders as plain text (a parser differential),
and it is unbounded, so a 30-digit token overflowed the DB INTEGER binding and
surfaced as a 500. This suite locks the six conversion sites to the shared
gate and proves malformed ids are rejected before any manager call.
"""

import pytest
from fastapi import HTTPException

import src.api as api_module
from src.api.routes.stickers import (
    _parse_sticker_id,
    delete_sticker,
    get_sticker_suggestions,
    send_sticker_usage,
)
from src.core import ratelimit


pytestmark = [pytest.mark.security, pytest.mark.unit]


class _User:
    user_id = 77


class _RLAllow:
    allowed = True
    retry_after = 0


class _StubStickers:
    def __init__(self, sticker=None, pack=None):
        self.sticker = sticker
        self.pack = pack
        self.get_sticker_calls = []
        self.suggestion_calls = []
        self.sent = []

    def get_sticker(self, sid):
        self.get_sticker_calls.append(sid)
        return self.sticker

    def get_pack(self, pack_id, user_id):
        return self.pack

    def remove_sticker(self, user_id, sid):
        return None

    def can_use_sticker(self, user_id, sid):
        return True

    def send_sticker(self, user_id, mid, sid):
        self.sent.append((user_id, mid, sid))

    def get_sticker_suggestions(self, user_id, content, sid, limit):
        self.suggestion_calls.append((user_id, content, sid, limit))
        return []


@pytest.fixture
def install_stub(monkeypatch):
    def _install(stub):
        monkeypatch.setattr(api_module, "get_stickers", lambda: stub)
        return stub

    return _install


@pytest.fixture
def rate_allowed(monkeypatch):
    async def _allow(**kwargs):
        return _RLAllow()

    monkeypatch.setattr(ratelimit, "acheck_rate_limit", _allow)


class TestParseStickerId:
    @pytest.mark.parametrize(
        "raw",
        [
            "١٢٣",  # Arabic-Indic homoglyphs of 123
            "１２３",  # fullwidth homoglyphs
            "1_2",  # PEP 515 separator
            "-1",  # negative
            "0",  # snowflake lower bound is 1
            "9" * 30,  # unbounded int -> would overflow DB INTEGER binding
            "abc",
        ],
    )
    def test_rejects_non_ascii_signed_zero_and_oversized(self, raw):
        with pytest.raises(HTTPException) as excinfo:
            _parse_sticker_id(raw, "sticker_id", "Invalid sticker ID")
        assert excinfo.value.status_code == 400
        assert excinfo.value.detail == "Invalid sticker ID"

    def test_accepts_canonical_decimal(self):
        assert _parse_sticker_id("42", "sticker_id", "Invalid sticker ID") == 42
        # Leading zeros are normalized by the shared gate, as elsewhere.
        assert _parse_sticker_id("00042", "sticker_id", "Invalid sticker ID") == 42


class TestSuggestionBoundary:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", ["١٢٣", "1_2", "9" * 30, "-5"])
    async def test_malformed_server_id_400_before_manager(self, install_stub, raw):
        stub = install_stub(_StubStickers())
        with pytest.raises(HTTPException) as excinfo:
            await get_sticker_suggestions(
                content="hello", server_id=raw, limit=5, current_user=_User()
            )
        assert excinfo.value.status_code == 400
        assert stub.suggestion_calls == []

    @pytest.mark.asyncio
    async def test_canonical_server_id_passes_through(self, install_stub):
        stub = install_stub(_StubStickers())
        await get_sticker_suggestions(
            content="hello", server_id="123", limit=5, current_user=_User()
        )
        assert stub.suggestion_calls == [(77, "hello", 123, 5)]


class TestDeleteBoundary:
    @pytest.mark.asyncio
    async def test_homoglyph_sticker_id_400_not_swallowed_as_500(self, install_stub):
        stub = install_stub(_StubStickers())
        with pytest.raises(HTTPException) as excinfo:
            await delete_sticker(sticker_id="٤٢", current_user=_User())
        # The catch-all in delete_sticker maps unexpected Exceptions to 500;
        # a validation rejection must keep its 400 and never touch the manager.
        assert excinfo.value.status_code == 400
        assert stub.get_sticker_calls == []

    @pytest.mark.asyncio
    async def test_oversized_sticker_id_400(self, install_stub):
        stub = install_stub(_StubStickers())
        with pytest.raises(HTTPException) as excinfo:
            await delete_sticker(sticker_id="9" * 30, current_user=_User())
        assert excinfo.value.status_code == 400
        assert stub.get_sticker_calls == []

    @pytest.mark.asyncio
    async def test_canonical_sticker_id_reaches_manager(self, install_stub):
        class _Sticker:
            pack_id = 555

        class _Pack:
            server_id = None

        stub = install_stub(_StubStickers(sticker=_Sticker(), pack=_Pack()))
        result = await delete_sticker(sticker_id="42", current_user=_User())
        assert result == {"success": True}
        assert stub.get_sticker_calls == [42]


class TestUsageBoundary:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("raw", ["٤٢", "1_0", "9" * 30])
    async def test_malformed_sticker_id_400_before_write(
        self, install_stub, rate_allowed, raw
    ):
        stub = install_stub(_StubStickers())
        with pytest.raises(HTTPException) as excinfo:
            await send_sticker_usage(
                sticker_id=raw,
                message_id="99",
                request=None,
                current_user=_User(),
            )
        # The catch-all in this route returns {"success": False} on any
        # Exception; a malformed id must surface as a clean 400 instead.
        assert excinfo.value.status_code == 400
        assert stub.sent == []

    @pytest.mark.asyncio
    async def test_malformed_message_id_400_before_write(
        self, install_stub, rate_allowed
    ):
        stub = install_stub(_StubStickers())
        with pytest.raises(HTTPException) as excinfo:
            await send_sticker_usage(
                sticker_id="42",
                message_id="９９",
                request=None,
                current_user=_User(),
            )
        assert excinfo.value.status_code == 400
        assert stub.sent == []

    @pytest.mark.asyncio
    async def test_canonical_ids_pass_through(self, install_stub, rate_allowed):
        stub = install_stub(_StubStickers())
        result = await send_sticker_usage(
            sticker_id="42", message_id="99", request=None, current_user=_User()
        )
        assert result == {"success": True}
        assert stub.sent == [(77, 99, 42)]
