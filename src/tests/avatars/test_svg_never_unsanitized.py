"""
Agent7 [L1] regression: SVG must never be stored/served unsanitized as an avatar.

Covers:
- defaults exclude SVG/HTML/JS avatar types;
- the avatar validator rejects ``image/svg+xml`` (and aliases) even when an
  operator adds it to ``avatars.allowed_types`` config;
- uploads claiming SVG - or smuggling SVG bytes under a raster MIME type -
  raise instead of storing verbatim (independent of Pillow behaviour);
- the serving path never returns a raw ``<script>``-carrying SVG, including
  poisoned legacy rows written directly to the DB and payloads that bypass
  the manager guard at the route layer.

These tests FAIL on the pre-fix code (validator honoured operator config,
no upload sniffing, ``get_*_data`` returned poisoned rows verbatim).
"""

import hashlib
import io
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.core.avatars.manager import AvatarManager
from src.core.avatars.manager.svg_guard import (
    NEVER_ALLOWED_AVATAR_CONTENT_TYPES,
    is_unsafe_serve_payload,
    is_unsafe_upload_payload,
)
from src.utils.encryption import generate_snowflake_id

MALICIOUS_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
    b"<script>alert(document.cookie)</script>"
    b'<rect width="10" height="10"/></svg>'
)

SPOOFED_SVG = MALICIOUS_SVG  # same bytes, claimed as image/png in tests below

FOREIGNOBJECT_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg">'
    b'<foreignObject><div xmlns="http://www.w3.org/1999/xhtml" '
    b'onclick="alert(1)">click</div></foreignObject></svg>'
)


def _make_png(width=32, height=32):
    from PIL import Image

    img = Image.new("RGB", (width, height), (0, 0, 255))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def avatar_manager(db):
    manager = AvatarManager(db=db)
    manager.setup(db)
    return manager


@pytest.fixture
def svg_allowed_config():
    """Simulate an operator adding SVG to the avatar allow-list."""
    from utils.config import _config_instance as loader

    loader.config["avatars"] = {
        "allowed_types": [
            "image/jpeg",
            "image/png",
            "image/gif",
            "image/webp",
            "image/svg+xml",
        ]
    }
    loader._invalidate_cache()
    try:
        yield
    finally:
        loader.config.pop("avatars", None)
        loader._invalidate_cache()


class TestDefaultsExcludeSvg:
    def test_class_defaults_exclude_svg(self):
        lowered = [t.lower() for t in AvatarManager.DEFAULT_ALLOWED_TYPES]
        assert "image/svg+xml" not in lowered
        assert "text/html" not in lowered

    def test_effective_defaults_exclude_svg(self, avatar_manager):
        allowed = [t.lower() for t in avatar_manager._get_allowed_types()]
        assert "image/svg+xml" not in allowed
        assert "application/svg+xml" not in allowed

    def test_block_list_covers_svg_aliases(self):
        assert "image/svg+xml" in NEVER_ALLOWED_AVATAR_CONTENT_TYPES
        assert "application/svg+xml" in NEVER_ALLOWED_AVATAR_CONTENT_TYPES
        assert "text/html" in NEVER_ALLOWED_AVATAR_CONTENT_TYPES


class TestValidatorRejectsSvgRegardlessOfConfig:
    @pytest.mark.parametrize(
        "content_type",
        [
            "image/svg+xml",
            "IMAGE/SVG+XML",
            "application/svg+xml",
            "text/html",
            "application/xhtml+xml",
            "application/javascript",
        ],
    )
    def test_blocked_even_when_operator_allows(
        self, avatar_manager, svg_allowed_config, content_type
    ):
        assert avatar_manager._validate_content_type(content_type) is False

    def test_raster_types_still_allowed(self, avatar_manager, svg_allowed_config):
        assert avatar_manager._validate_content_type("image/png") is True
        assert avatar_manager._validate_content_type("image/jpeg") is True

    def test_upload_guard_classifies_operator_svg(self, svg_allowed_config):
        assert is_unsafe_upload_payload("image/svg+xml", MALICIOUS_SVG) is True
        assert is_unsafe_upload_payload("image/svg+xml", b"<svg></svg>") is True


class TestUploadNeverStoresSvg:
    def test_process_image_rejects_svg_bytes(self, avatar_manager, svg_allowed_config):
        with pytest.raises(ValueError):
            avatar_manager._process_image(MALICIOUS_SVG, "image/svg+xml")

    def test_process_image_rejects_spoofed_svg_as_png(self, avatar_manager):
        # No operator config needed: sniffing applies to every upload.
        with pytest.raises(ValueError):
            avatar_manager._process_image(SPOOFED_SVG, "image/png")

    def test_process_image_rejects_foreignobject(self, avatar_manager):
        with pytest.raises(ValueError):
            avatar_manager._process_image(FOREIGNOBJECT_SVG, "image/png")

    def test_upload_rejected_before_processing(
        self, avatar_manager, svg_allowed_config, test_user
    ):
        """Validation must reject SVG even if image processing would store it.

        Simulates the Pillow-missing verbatim path (or a future Pillow with
        SVG support): with processing stubbed to "succeed", the upload must
        still refuse - pre-fix code stored the SVG verbatim here.
        """
        with patch.object(
            avatar_manager,
            "_process_image",
            return_value=(MALICIOUS_SVG, 64, 64, False),
        ):
            with pytest.raises(ValueError):
                avatar_manager.upload_user_avatar(
                    test_user.id, MALICIOUS_SVG, "image/svg+xml"
                )
        assert avatar_manager.get_user_avatar(test_user.id) is None

    def test_upload_user_avatar_rejects_svg_end_to_end(
        self, avatar_manager, svg_allowed_config, test_user
    ):
        with pytest.raises(ValueError):
            avatar_manager.upload_user_avatar(
                test_user.id, MALICIOUS_SVG, "image/svg+xml"
            )
        assert avatar_manager.get_user_avatar_data(test_user.id) is None

    def test_upload_server_icon_rejects_svg_end_to_end(
        self, avatar_manager, svg_allowed_config, test_server
    ):
        server, _owner = test_server
        with pytest.raises(ValueError):
            avatar_manager.upload_server_icon(server.id, MALICIOUS_SVG, "image/svg+xml")
        assert avatar_manager.get_server_icon_data(server.id) is None

    def test_upload_rejects_spoofed_svg_end_to_end(self, avatar_manager, test_user):
        with pytest.raises(ValueError):
            avatar_manager.upload_user_avatar(test_user.id, SPOOFED_SVG, "image/png")

    def test_legit_png_still_uploads(
        self, avatar_manager, svg_allowed_config, test_user
    ):
        result = avatar_manager.upload_user_avatar(
            test_user.id, _make_png(), "image/png"
        )
        assert result["content_type"] == "image/png"


class TestServingNeverReturnsRawScriptSvg:
    def _poison_user_row(self, db, user_id):
        checksum = hashlib.sha256(MALICIOUS_SVG).hexdigest()
        db.execute(
            """
            INSERT INTO user_avatars
            (id, user_id, avatar_data, content_type, width, height, size,
             checksum, animated, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generate_snowflake_id(),
                user_id,
                MALICIOUS_SVG,
                "image/svg+xml",
                10,
                10,
                len(MALICIOUS_SVG),
                checksum,
                0,
                int(time.time() * 1000),
            ),
        )

    def _poison_server_row(self, db, server_id):
        checksum = hashlib.sha256(MALICIOUS_SVG).hexdigest()
        db.execute(
            """
            INSERT INTO server_icons
            (id, server_id, icon_data, content_type, width, height, size,
             checksum, animated, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generate_snowflake_id(),
                server_id,
                MALICIOUS_SVG,
                "image/svg+xml",
                10,
                10,
                len(MALICIOUS_SVG),
                checksum,
                0,
                int(time.time() * 1000),
            ),
        )

    def test_user_data_guard_blocks_legacy_poison_row(
        self, avatar_manager, db, test_user
    ):
        self._poison_user_row(db, test_user.id)
        assert avatar_manager.get_user_avatar_data(test_user.id) is None

    def test_server_data_guard_blocks_legacy_poison_row(
        self, avatar_manager, db, test_server
    ):
        server, _owner = test_server
        self._poison_server_row(db, server.id)
        assert avatar_manager.get_server_icon_data(server.id) is None

    def test_spoof_labelled_poison_row_blocked(self, avatar_manager, db, test_user):
        """A legacy row labelled image/png whose bytes are SVG+script."""
        checksum = hashlib.sha256(SPOOFED_SVG).hexdigest()
        db.execute(
            """
            INSERT INTO user_avatars
            (id, user_id, avatar_data, content_type, width, height, size,
             checksum, animated, uploaded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                generate_snowflake_id(),
                test_user.id,
                SPOOFED_SVG,
                "image/png",
                10,
                10,
                len(SPOOFED_SVG),
                checksum,
                0,
                int(time.time() * 1000),
            ),
        )
        assert avatar_manager.get_user_avatar_data(test_user.id) is None

    def test_serve_classifier_flags_script_svg(self):
        assert is_unsafe_serve_payload("image/svg+xml", MALICIOUS_SVG) is True
        assert is_unsafe_serve_payload("image/png", SPOOFED_SVG) is True
        assert is_unsafe_serve_payload("image/svg+xml", FOREIGNOBJECT_SVG) is True

    def test_serve_classifier_allows_benign_raster(self):
        png = _make_png()
        assert is_unsafe_serve_payload("image/png", png) is False
        assert is_unsafe_upload_payload("image/png", png) is False

    @pytest.mark.asyncio
    async def test_user_route_never_serves_raw_script_svg(self, test_user):
        """Route-level defense in depth: poison bypassing the manager guard."""
        from src.api.routes.avatars import get_user_avatar

        fake_avatars = MagicMock()
        fake_avatars.get_user_avatar_checksum.return_value = None
        # Simulate a poisoned payload reaching the route layer directly.
        fake_avatars.get_user_avatar_data.return_value = (
            MALICIOUS_SVG,
            "image/svg+xml",
            "deadbeef",
        )
        request = SimpleNamespace(headers={})
        with (
            patch("src.api.get_avatars", return_value=fake_avatars),
            patch(
                "src.core.avatars.generate_default_svg",
                return_value='<svg width="128" height="128"></svg>',
            ),
        ):
            response = await get_user_avatar(test_user.id, request)
        assert b"<script" not in response.body
        assert b"onload" not in response.body

    @pytest.mark.asyncio
    async def test_server_route_never_serves_raw_script_svg(self, test_server):
        from src.api.routes.avatars import get_server_icon

        server, _owner = test_server
        fake_avatars = MagicMock()
        fake_avatars.get_server_icon_checksum.return_value = None
        fake_avatars.get_server_icon_data.return_value = (
            MALICIOUS_SVG,
            "image/svg+xml",
            "deadbeef",
        )
        request = SimpleNamespace(headers={})
        with (
            patch("src.api.get_avatars", return_value=fake_avatars),
            patch(
                "src.core.avatars.generate_default_svg",
                return_value='<svg width="128" height="128"></svg>',
            ),
        ):
            response = await get_server_icon(server.id, request)
        assert b"<script" not in response.body
        assert b"onload" not in response.body
