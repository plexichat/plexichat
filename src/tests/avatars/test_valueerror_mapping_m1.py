"""M1 regression: (ValueError, TypeError) from image processing must map to
honest 400 with a static generic message on every avatar/icon upload route.

Covers:
- src.api.routes.avatars.upload_my_avatar
- src.api.routes.avatars.upload_server_icon
- src.api.routes.users.avatar.AvatarMixin.upload_avatar (composer variant)
- src.api.routes.servers.icon_upload.upload_server_icon (servers variant)
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _upload_file(payload: bytes = b"garbage"):
    return SimpleNamespace(
        content_type="image/png", read=AsyncMock(return_value=payload)
    )


def _assert_honest_400(exc: HTTPException, secret: str):
    assert exc.status_code == 400
    detail = str(exc.detail)
    assert "Invalid image file" in detail
    # Must never leak the underlying exception text.
    assert secret not in detail


@pytest.mark.asyncio
async def test_avatars_route_user_avatar_maps_valueerror_and_typeerror():
    from src.api.routes.avatars import upload_my_avatar

    for exc_cls, secret in (
        (ValueError, "super-secret-value"),
        (TypeError, "super-secret-type"),
    ):
        fake_avatars = MagicMock()
        fake_avatars.upload_user_avatar.side_effect = exc_cls(secret)
        with patch("src.api.get_avatars", return_value=fake_avatars):
            with pytest.raises(HTTPException) as ei:
                await upload_my_avatar(_upload_file(), SimpleNamespace(user_id=1))
        _assert_honest_400(ei.value, secret)


@pytest.mark.asyncio
async def test_avatars_route_server_icon_maps_valueerror_and_typeerror():
    from src.api.routes.avatars import upload_server_icon

    for exc_cls, secret in (
        (ValueError, "super-secret-value"),
        (TypeError, "super-secret-type"),
    ):
        fake_avatars = MagicMock()
        fake_avatars.upload_server_icon.side_effect = exc_cls(secret)
        fake_servers = MagicMock()
        fake_servers.get_server.return_value = SimpleNamespace(owner_id=1)
        with (
            patch("src.api.get_avatars", return_value=fake_avatars),
            patch("src.api.get_servers", return_value=fake_servers),
        ):
            with pytest.raises(HTTPException) as ei:
                await upload_server_icon(
                    "9", _upload_file(), SimpleNamespace(user_id=1)
                )
        _assert_honest_400(ei.value, secret)


@pytest.mark.asyncio
async def test_composer_route_avatar_maps_valueerror_and_typeerror():
    from src.api.routes.users.avatar import AvatarMixin

    for exc_cls, secret in (
        (ValueError, "super-secret-value"),
        (TypeError, "super-secret-type"),
    ):
        fake_avatars = MagicMock()
        fake_avatars.upload_user_avatar.side_effect = exc_cls(secret)
        with patch("src.api.get_avatars", return_value=fake_avatars):
            with pytest.raises(HTTPException) as ei:
                await AvatarMixin().upload_avatar(
                    _upload_file(), SimpleNamespace(user_id=1)
                )
        _assert_honest_400(ei.value, secret)


@pytest.mark.asyncio
async def test_servers_icon_upload_route_maps_valueerror_and_typeerror():
    from src.api.routes.servers.icon_upload import (
        upload_server_icon as servers_icon_upload,
    )

    for exc_cls, secret in (
        (ValueError, "super-secret-value"),
        (TypeError, "super-secret-type"),
    ):
        fake_avatars = MagicMock()
        fake_avatars.upload_server_icon.side_effect = exc_cls(secret)
        fake_servers = MagicMock()
        fake_servers.get_server.return_value = SimpleNamespace(owner_id=1)
        fake_servers.require_permission.return_value = True
        fake_media = MagicMock()
        with (
            patch("src.api.routes.servers.icon_upload.api") as mock_api,
        ):
            mock_api.get_servers.return_value = fake_servers
            mock_api.get_media.return_value = fake_media
            mock_api.get_avatars.return_value = fake_avatars
            with pytest.raises(HTTPException) as ei:
                await servers_icon_upload(
                    "9", _upload_file(), SimpleNamespace(user_id=1)
                )
        _assert_honest_400(ei.value, secret)
