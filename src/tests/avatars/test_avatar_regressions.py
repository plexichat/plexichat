"""
Regression tests for avatar cluster fixes.

Covers:
- Animated GIF/WebP frames preserved on re-encode (no silent frame loss)
- Static GIFs keep their container + transparency (no lossy JPEG coercion)
- Invalid-image uploads map to a consistent 400 on every upload endpoint
- Avatar/icon changes reach connected clients via gateway events
"""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.core.events.types import EventType


def _make_png(width=64, height=64, color=(255, 0, 0)):
    """Create a tiny real PNG in memory."""
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _make_animated_gif(frames=3):
    """Create a real animated GIF in memory."""
    from PIL import Image

    imgs = [Image.new("RGB", (16, 16), (i * 60, 0, 0)) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(
        buf,
        format="GIF",
        save_all=True,
        append_images=imgs[1:],
        duration=100,
        loop=0,
    )
    return buf.getvalue()


def _make_static_gif_with_transparency():
    """Create a palette GIF whose color 0 is transparent."""
    from PIL import Image

    img = Image.new("P", (16, 16))
    palette = [0, 0, 0, 255, 255, 255] + [0, 0, 0] * 254
    img.putpalette(palette)
    img.info["transparency"] = 0
    buf = io.BytesIO()
    img.save(buf, format="GIF")
    return buf.getvalue()


def _make_animated_webp(frames=3):
    """Create a real animated WebP in memory."""
    from PIL import Image

    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    imgs = [Image.new("RGB", (16, 16), colors[i % len(colors)]) for i in range(frames)]
    buf = io.BytesIO()
    imgs[0].save(
        buf,
        format="WEBP",
        save_all=True,
        append_images=imgs[1:],
        duration=100,
    )
    return buf.getvalue()


@pytest.fixture
def avatar_manager(db):
    """A configured AvatarManager bound to the test db."""
    from src.core.avatars.manager import AvatarManager

    manager = AvatarManager(db=db)
    manager.setup(db)
    return manager


class TestAnimatedFramePreservation:
    """M2: animated formats must keep every frame."""

    def test_animated_gif_keeps_all_frames_without_resize(self, avatar_manager):
        from PIL import Image

        processed, _, _, animated = avatar_manager._process_image(
            _make_animated_gif(), "image/gif"
        )
        assert animated is True
        out = Image.open(io.BytesIO(processed))
        assert getattr(out, "n_frames", 1) == 3

    def test_animated_webp_keeps_all_frames_without_resize(self, avatar_manager):
        from PIL import Image

        processed, _, _, animated = avatar_manager._process_image(
            _make_animated_webp(), "image/webp"
        )
        assert animated is True
        out = Image.open(io.BytesIO(processed))
        assert getattr(out, "n_frames", 1) == 3

    def test_animated_gif_resized_keeps_all_frames(self, avatar_manager):
        from utils.config import _config_instance as loader

        loader.config["avatars"] = {"max_size": 8}
        loader._invalidate_cache()
        try:
            processed, width, height, animated = avatar_manager._process_image(
                _make_animated_gif(), "image/gif"
            )
            assert animated is True
            assert width <= 8 and height <= 8
        finally:
            loader.config.pop("avatars", None)
            loader._invalidate_cache()

        from PIL import Image

        out = Image.open(io.BytesIO(processed))
        assert getattr(out, "n_frames", 1) == 3


class TestStaticGifContainer:
    """M4: static GIFs must stay GIFs (not be coerced to lossy JPEG)."""

    def test_static_gif_stays_gif(self, avatar_manager):
        processed, _, _, animated = avatar_manager._process_image(
            _make_animated_gif(1), "image/gif"
        )
        assert animated is False
        assert processed[:6] in (b"GIF87a", b"GIF89a")

    def test_transparent_gif_preserved(self, avatar_manager):
        processed, _, _, animated = avatar_manager._process_image(
            _make_static_gif_with_transparency(), "image/gif"
        )
        assert animated is False
        # Must NOT be JPEG (lossy conversion would destroy transparency).
        assert processed[:6] in (b"GIF87a", b"GIF89a")
        assert not processed.startswith(b"\xff\xd8\xff")


class TestConsistentInvalidImageErrors:
    """M1: every upload endpoint maps invalid images to a 400."""

    @pytest.mark.asyncio
    async def test_avatars_route_upload_my_avatar_400(self):
        from src.api.routes.avatars import upload_my_avatar

        fake_avatars = MagicMock()
        fake_avatars.upload_user_avatar.side_effect = ValueError("bad image")
        file = SimpleNamespace(
            content_type="image/png", read=AsyncMock(return_value=b"garbage")
        )
        current_user = SimpleNamespace(user_id=1)
        with patch("src.api.get_avatars", return_value=fake_avatars):
            with pytest.raises(HTTPException) as exc:
                await upload_my_avatar(file, current_user)
        assert exc.value.status_code == 400
        assert "Invalid image file" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_composer_route_upload_avatar_400(self):
        from src.api.routes.users.avatar import AvatarMixin

        fake_avatars = MagicMock()
        fake_avatars.upload_user_avatar.side_effect = ValueError("bad image")
        file = SimpleNamespace(
            content_type="image/png", read=AsyncMock(return_value=b"garbage")
        )
        current_user = SimpleNamespace(user_id=1)
        with patch("src.api.get_avatars", return_value=fake_avatars):
            with pytest.raises(HTTPException) as exc:
                await AvatarMixin().upload_avatar(file, current_user)
        assert exc.value.status_code == 400
        assert "Invalid image file" in str(exc.value.detail)


class TestGatewayEmitParity:
    """H2: avatar/icon changes must reach connected clients."""

    def test_emit_guild_update_dispatches_with_icon(self):
        from src.core.events.gateway_emit import emit_guild_update

        with (
            patch("src.core.events.is_setup", return_value=True),
            patch("src.core.events.dispatch") as dispatch,
        ):
            emit_guild_update(42, icon="/api/v1/avatars/servers/42")

        event = dispatch.call_args.args[0]
        assert event.event_type == EventType.GUILD_UPDATE
        assert event.data["id"] == "42"
        assert event.data["icon"] == "/api/v1/avatars/servers/42"

    def test_emit_guild_update_allows_explicit_clear(self):
        from src.core.events.gateway_emit import emit_guild_update

        with (
            patch("src.core.events.is_setup", return_value=True),
            patch("src.core.events.dispatch") as dispatch,
        ):
            emit_guild_update(42, icon="")

        event = dispatch.call_args.args[0]
        assert event.data["icon"] == ""

    @pytest.mark.asyncio
    async def test_avatar_route_upload_emits_user_update(self):
        from src.api.routes.avatars import upload_my_avatar

        fake_avatars = MagicMock()
        fake_avatars.upload_user_avatar.return_value = {
            "url": "/api/v1/avatars/users/1",
            "width": 16,
            "height": 16,
            "size": 100,
            "animated": False,
        }
        file = SimpleNamespace(
            content_type="image/png", read=AsyncMock(return_value=_make_png())
        )
        current_user = SimpleNamespace(user_id=1)
        with (
            patch("src.api.get_avatars", return_value=fake_avatars),
            patch("src.core.events.gateway_emit.emit_user_update") as emit,
        ):
            await upload_my_avatar(file, current_user)

        emit.assert_called_once()
        payload = emit.call_args.args[0]
        assert payload["avatar_url"] == "/api/v1/avatars/users/1"

    @pytest.mark.asyncio
    async def test_icon_upload_emits_guild_update(self):
        from src.api.routes.avatars import upload_server_icon

        fake_avatars = MagicMock()
        fake_avatars.upload_server_icon.return_value = {
            "url": "/api/v1/avatars/servers/9",
            "width": 16,
            "height": 16,
            "size": 100,
            "animated": False,
        }
        fake_servers = MagicMock()
        fake_servers.get_server.return_value = SimpleNamespace(owner_id=1)
        fake_servers.has_permission.return_value = False
        file = SimpleNamespace(
            content_type="image/png", read=AsyncMock(return_value=_make_png())
        )
        current_user = SimpleNamespace(user_id=1)
        with (
            patch("src.api.get_avatars", return_value=fake_avatars),
            patch("src.api.get_servers", return_value=fake_servers),
            patch("src.core.events.gateway_emit.emit_guild_update") as emit,
        ):
            await upload_server_icon("9", file, current_user)

        emit.assert_called_once()
        assert emit.call_args.args[0] == 9
        assert emit.call_args.kwargs["icon"] == "/api/v1/avatars/servers/9"
