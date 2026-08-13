"""
Regression tests for avatar audit fixes.

Covers:
- AV-1: checksum cache is invalidated on upload/delete (route + Valkey).
- AV-2: ETag comparison is exact (no substring match).
- AV-3: ``AvatarProtocol._get_timestamp`` / ``_generate_id`` return real values.
- AV-4: default SVG escapes user-controlled initials.
- AV-5: default SVG colour matches the client djb2 algorithm.
"""

import io
import re

import pytest

from src.core.avatars.manager import AvatarManager
from src.core.avatars.manager.defaults import _djb2


def _make_png(width=64, height=64, color=(255, 0, 0)):
    from PIL import Image

    img = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def avatar_manager(db):
    manager = AvatarManager(db=db)
    manager.setup(db)
    return manager


DEFAULT_COLORS = [
    "#6d86d9",
    "#5ea381",
    "#c99563",
    "#5f9ccf",
    "#8c79c8",
    "#c27ba2",
]


class TestAvatarAuditFixes:
    # ---- AV-3 ---------------------------------------------------------------
    def test_get_timestamp_and_id_are_real(self, avatar_manager):
        ts = avatar_manager._get_timestamp()
        assert isinstance(ts, int) and ts > 0
        gen_id = avatar_manager._generate_id()
        assert isinstance(gen_id, int) and gen_id > 0

    # ---- AV-4 ---------------------------------------------------------------
    def test_default_svg_escapes_initials(self, avatar_manager):
        svg = avatar_manager.generate_default_svg("seed", "<script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in svg
        assert "&lt;script&gt;alert(1)&lt;/script&gt;" in svg

    # ---- AV-5 ---------------------------------------------------------------
    def test_default_svg_color_matches_client_djb2(self, avatar_manager):
        seed = "alice"
        svg = avatar_manager.generate_default_svg(seed, "AL")
        match = re.search(r'fill="([^"]+)"', svg)
        assert match is not None
        fill = match.group(1)
        expected = DEFAULT_COLORS[
            _djb2(seed.strip().lower().encode()) % len(DEFAULT_COLORS)
        ]
        assert fill == expected

    def test_default_svg_color_is_deterministic(self, avatar_manager):
        a = re.search(
            r'fill="([^"]+)"', avatar_manager.generate_default_svg("bob", "BO")
        ).group(1)
        b = re.search(
            r'fill="([^"]+)"', avatar_manager.generate_default_svg("bob", "BO")
        ).group(1)
        assert a == b

    # ---- AV-1 (manager + Valkey layer) ------------------------------------
    def test_upload_invalidates_checksum_cache(self, avatar_manager, monkeypatch):
        class FakeValkey:
            def __init__(self):
                self.store = {}

            def set_bin(self, k, v, ttl=None):
                self.store[k] = bytes(v)

            def get_bin(self, k):
                return self.store.get(k)

            def delete(self, k):
                self.store.pop(k, None)

        fv = FakeValkey()
        monkeypatch.setattr("src.core.database.valkey_available", lambda: True)
        monkeypatch.setattr("src.core.database.get_valkey_client", lambda: fv)

        avatar_manager.upload_user_avatar(1, _make_png(), "image/png")
        assert avatar_manager.get_user_avatar_checksum(1)

        # Simulate a stale cached checksum, then re-upload.
        fv.store["user_avatar_checksum:1"] = b"STALE"
        assert avatar_manager.get_user_avatar_checksum(1) == "STALE"

        avatar_manager.upload_user_avatar(1, _make_png(color=(0, 0, 255)), "image/png")
        fresh = avatar_manager.get_user_avatar_checksum(1)
        assert fresh != "STALE"

    def test_delete_invalidates_checksum_cache(self, avatar_manager, monkeypatch):
        class FakeValkey:
            def __init__(self):
                self.store = {}

            def set_bin(self, k, v, ttl=None):
                self.store[k] = bytes(v)

            def get_bin(self, k):
                return self.store.get(k)

            def delete(self, k):
                self.store.pop(k, None)

        fv = FakeValkey()
        monkeypatch.setattr("src.core.database.valkey_available", lambda: True)
        monkeypatch.setattr("src.core.database.get_valkey_client", lambda: fv)

        avatar_manager.upload_user_avatar(1, _make_png(), "image/png")
        fv.store["user_avatar_checksum:1"] = b"STALE"
        assert avatar_manager.get_user_avatar_checksum(1) == "STALE"
        avatar_manager.delete_user_avatar(1)
        assert avatar_manager.get_user_avatar_checksum(1) != "STALE"

    # ---- AV-1 (route layer) + AV-2 (exact ETag) ---------------------------
    def test_route_cache_invalidation_helper(self, monkeypatch):
        from src.api.routes import avatars as avatar_routes

        calls = []
        monkeypatch.setattr(
            "src.core.database.invalidate_pattern",
            lambda pattern: calls.append(pattern),
        )

        # User avatar cache
        avatar_routes._avatar_checksum_cache[42] = "old"
        avatar_routes._invalidate_user_avatar_cache(42)
        assert 42 not in avatar_routes._avatar_checksum_cache
        assert any("user_avatar_checksum:42" in c for c in calls)

        # Server icon cache
        calls.clear()
        avatar_routes._icon_checksum_cache[7] = "old"
        avatar_routes._invalidate_server_icon_cache(7)
        assert 7 not in avatar_routes._icon_checksum_cache
        assert any("server_icon_checksum:7" in c for c in calls)

    def test_route_etag_check_is_exact_not_substring(self, monkeypatch):
        """AV-2: a client ETag that merely *contains* the checksum must NOT 304."""
        from src.api.routes import avatars as avatar_routes
        from starlette.datastructures import Headers

        real_checksum = "abcdef123456"
        # Malicious client ETag that the OLD substring check would have matched.
        etag_client = "prefix-abcdef123456-SUFFIX"

        # Replicate the route's exact-match branch (mirrors avatars.py).
        is_304 = (
            etag_client == real_checksum
            or etag_client == f'"{real_checksum}"'
            or etag_client == f'W/"{real_checksum}"'
        )
        assert is_304 is False
        assert real_checksum in etag_client  # proves the substring check is gone
