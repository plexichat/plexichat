"""Regressions for server-route hardening: bounded icon uploads and
immediate revocation of cached per-viewer access on membership/role changes.
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.api.routes.servers import bans, icon_upload, members, misc
from src.api.routes.servers.helpers import (
    DEFAULT_ICON_SIZE_LIMIT,
    _get_icon_size_limit,
)


class _Token:
    user_id = 1


class _FakeUpload:
    """UploadFile stand-in that records how ``read`` was called."""

    def __init__(self, data: bytes, declared_size=None):
        self.data = data
        self.size = declared_size
        self.content_type = "image/png"
        self.reads = []

    async def read(self, size=-1):
        self.reads.append(size)
        if size is None or size < 0:
            return self.data
        return self.data[:size]


def _icon_route_modules():
    servers = MagicMock()
    servers.get_server.return_value = MagicMock()
    servers.require_permission.return_value = None
    return servers


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", [None, "text/html", "application/svg+xml", ""])
async def test_icon_upload_rejects_non_image_parts_before_reading(content_type):
    upload = _FakeUpload(b"payload", declared_size=None)
    upload.content_type = content_type
    with (
        patch.object(
            icon_upload.api, "get_servers", return_value=_icon_route_modules()
        ),
        patch.object(icon_upload.api, "get_media", return_value=MagicMock()),
        patch.object(icon_upload.api, "get_avatars", return_value=MagicMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await icon_upload.upload_server_icon("1", upload, _Token())

    assert exc.value.status_code == 400
    assert upload.reads == [], "non-image part must be refused without reading"


@pytest.mark.asyncio
async def test_icon_upload_read_is_bounded_by_configured_limit():
    """A huge multipart body must never be materialised with an unbounded read."""
    limit = _get_icon_size_limit()
    assert limit > 0
    # Declared size is hidden to make sure the *read* itself is capped too,
    # not just the Content-Length-style declaration.
    upload = _FakeUpload(b"x" * (limit + 1), declared_size=None)

    with (
        patch.object(
            icon_upload.api, "get_servers", return_value=_icon_route_modules()
        ),
        patch.object(icon_upload.api, "get_media", return_value=MagicMock()),
        patch.object(icon_upload.api, "get_avatars", return_value=MagicMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await icon_upload.upload_server_icon("1", upload, _Token())

    assert exc.value.status_code == 413
    assert upload.reads == [limit + 1], upload.reads


@pytest.mark.asyncio
async def test_icon_upload_rejects_inflated_declared_size_without_reading():
    limit = _get_icon_size_limit()
    upload = _FakeUpload(b"tiny", declared_size=limit + 1)

    with (
        patch.object(
            icon_upload.api, "get_servers", return_value=_icon_route_modules()
        ),
        patch.object(icon_upload.api, "get_media", return_value=MagicMock()),
        patch.object(icon_upload.api, "get_avatars", return_value=MagicMock()),
    ):
        with pytest.raises(HTTPException) as exc:
            await icon_upload.upload_server_icon("1", upload, _Token())

    assert exc.value.status_code == 413
    assert upload.reads == [], "must refuse before reading the part"


@pytest.mark.parametrize("value", ["not-an-int", 0, -5, None])
def test_icon_size_limit_fails_closed(monkeypatch, value):
    from src.api.routes.servers import helpers

    monkeypatch.setattr(
        helpers.config,
        "get",
        lambda *a, **k: {"media": {"size_limits": {"icon": value}}},
    )
    assert _get_icon_size_limit() == DEFAULT_ICON_SIZE_LIMIT


def test_icon_route_uses_the_cap_helper():
    body = inspect.getsource(icon_upload.upload_server_icon)
    assert "read_upload_capped" in body
    assert "await file.read()" not in body
    assert "_get_icon_size_limit()" in body


class _Db:
    def __init__(self, user_id=2):
        self.user_id = user_id

    def fetch_one(self, sql, params):
        return {"user_id": self.user_id}


@pytest.mark.asyncio
async def test_kick_invalidates_cached_member_access():
    servers = MagicMock()
    servers.kick_member.return_value = None
    with (
        patch.object(members.api, "get_servers", return_value=servers),
        patch.object(members.api, "get_db", return_value=_Db()),
        patch.object(members, "invalidate_pattern") as invalidate,
    ):
        result = await members.kick_member("1", "10", _Token())

    assert result.success is True
    prefixes = {call.args[0] for call in invalidate.call_args_list}
    # Kicking changes the member list and the channel list; the cached
    # sticker/sound/emoji listings of the server are scoped to its members too.
    assert prefixes == {
        "server_members_api:*",
        "user_servers_api:*",
        "server_channels_api:*",
        "server_stickers_api:*",
        "server_sounds_api:*",
        "emoji_list_api:*",
        "emoji_detail_api:*",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "route_name", ["assign_role_to_member", "remove_role_from_member"]
)
async def test_role_changes_invalidate_cached_role_listing(route_name):
    servers = MagicMock()
    getattr(
        servers, "assign_role" if route_name.startswith("assign") else "remove_role"
    ).return_value = None
    with (
        patch.object(members.api, "get_servers", return_value=servers),
        patch.object(members.api, "get_db", return_value=_Db()),
        patch.object(members, "_invalidate_roles_listing_cache") as invalidate_roles,
    ):
        result = await getattr(members, route_name)("1", "10", "20", _Token())

    assert result.success is True
    invalidate_roles.assert_called_once_with()


@pytest.mark.asyncio
async def test_ban_invalidates_cached_member_access():
    servers = MagicMock()
    servers.ban_member.return_value = None
    with (
        patch.object(bans.api, "get_servers", return_value=servers),
        patch.object(bans, "_invalidate_member_access_caches") as invalidate,
    ):
        result = await bans.ban_member("1", "2", None, _Token())

    assert result.success is True
    invalidate.assert_called_once_with()


@pytest.mark.asyncio
async def test_leave_invalidates_cached_member_access():
    servers = MagicMock()
    servers.leave_server.return_value = None
    with (
        patch.object(misc.api, "get_servers", return_value=servers),
        patch.object(misc, "invalidate_user_servers"),
        patch.object(misc, "_invalidate_member_access_caches") as invalidate,
    ):
        result = await misc.leave_server("1", _Token())

    assert result.success is True
    invalidate.assert_called_once_with()
