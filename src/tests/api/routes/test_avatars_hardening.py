"""
Hardening + split compatibility tests for ``src.api.routes.avatars``.

Covers:
- package public API surface and backward-compatible import paths;
- validation rejection cases (oversized files, disallowed MIME, SVG
  magic-byte smuggling, blocked content types, path-traversal server ids);
- security hardening (``is_serve_safe`` fail-closed, safe filenames);
- internal helper behaviors (bounded checksum cache, plain-dict cache path).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.api.routes import avatars as avatar_routes
from src.api.routes.avatars.errors import (
    AvatarError,
    AvatarValidationError,
    AvatarNotFoundError,
    AvatarStorageError,
    AvatarUnauthorizedError,
    AvatarForbiddenError,
)
from src.api.routes.avatars.models import (
    metadata_from_manager,
    upload_result_from_manager,
)
from src.api.routes.avatars.validation import (
    validate_avatar_upload,
    validate_upload_magic_bytes,
    validate_upload_size,
    is_serve_safe,
    parse_server_id,
    sanitize_filename,
    max_file_size,
    allowed_types,
    is_safe_avatar_identifier,
)
from src.api.routes.avatars.storage import (
    _BoundedChecksumCache,
    _cache_set_checksum,
    _avatar_checksum_cache,
    _icon_checksum_cache,
    _MAX_CHECKSUM_CACHE_ENTRIES,
)


MALICIOUS_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
    b"<script>alert(document.cookie)</script>"
    b'<rect width="10" height="10"/></svg>'
)
BENIGN_PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _upload_file(payload: bytes = BENIGN_PNG_HEADER, content_type: str = "image/png"):
    return SimpleNamespace(
        content_type=content_type, read=AsyncMock(return_value=payload)
    )


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


def test_package_exposes_public_api():
    expected = [
        "router",
        "get_user_avatar",
        "upload_my_avatar",
        "delete_my_avatar",
        "get_server_icon",
        "upload_server_icon",
        "delete_server_icon",
        "validate_avatar_upload",
        "is_serve_safe",
        "parse_server_id",
        "sanitize_filename",
        "_BoundedChecksumCache",
        "_cache_set_checksum",
        "_avatar_checksum_cache",
        "_icon_checksum_cache",
        "_MAX_CHECKSUM_CACHE_ENTRIES",
        "_invalidate_user_avatar_cache",
        "_invalidate_server_icon_cache",
        "AvatarValidationError",
    ]
    for name in expected:
        assert hasattr(avatar_routes, name), f"missing {name}"


def test_router_is_api_router():
    from fastapi import APIRouter

    assert isinstance(avatar_routes.router, APIRouter)


def test_all_names_in_all():
    assert "__all__" in dir(avatar_routes)
    for name in avatar_routes.__all__:
        assert hasattr(avatar_routes, name), f"__all__ entry missing: {name}"


# ---------------------------------------------------------------------------
# Compat import paths (drop-in replacement for the old module)
# ---------------------------------------------------------------------------


def test_compat_import_from_routes_package():
    from src.api.routes.avatars import (
        upload_my_avatar,
        upload_server_icon,
        get_user_avatar,
        get_server_icon,
    )

    assert callable(upload_my_avatar)
    assert callable(upload_server_icon)
    assert callable(get_user_avatar)
    assert callable(get_server_icon)


def test_compat_import_private_cache_names():
    assert avatar_routes._avatar_checksum_cache is _avatar_checksum_cache
    assert avatar_routes._icon_checksum_cache is _icon_checksum_cache
    assert avatar_routes._MAX_CHECKSUM_CACHE_ENTRIES == _MAX_CHECKSUM_CACHE_ENTRIES


# ---------------------------------------------------------------------------
# Validation: rejection cases
# ---------------------------------------------------------------------------


def test_validation_rejects_oversized_file():
    oversized = b"x" * (max_file_size() + 1)
    with pytest.raises(AvatarValidationError):
        validate_avatar_upload("image/png", oversized)


def test_validation_rejects_disallowed_mime():
    with pytest.raises(AvatarValidationError):
        validate_avatar_upload("image/bmp", BENIGN_PNG_HEADER)
    with pytest.raises(AvatarValidationError):
        validate_avatar_upload("text/plain", b"not an image")


def test_validation_rejects_blocked_content_types():
    for ct in (
        "image/svg+xml",
        "image/svg",
        "application/svg+xml",
        "text/html",
        "application/javascript",
    ):
        with pytest.raises(AvatarValidationError):
            validate_avatar_upload(ct, BENIGN_PNG_HEADER)


def test_validation_rejects_svg_smuggled_as_png():
    with pytest.raises(AvatarValidationError):
        validate_avatar_upload("image/png", MALICIOUS_SVG)
    with pytest.raises(AvatarValidationError):
        validate_avatar_upload("image/png", b"<svg></svg>")


def test_validation_accepts_valid_raster():
    # Real raster bytes never start with markup, so they pass magic-byte
    # sniffing and are within the size limit.
    validate_avatar_upload("image/png", BENIGN_PNG_HEADER)
    validate_avatar_upload("image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    validate_avatar_upload("image/gif", b"GIF89a" + b"\x00" * 64)
    validate_avatar_upload("image/webp", b"RIFF" + b"\x00" * 64)


def test_validate_upload_magic_bytes_rejects_markup():
    with pytest.raises(AvatarValidationError):
        validate_upload_magic_bytes("image/png", MALICIOUS_SVG)
    with pytest.raises(AvatarValidationError):
        validate_upload_magic_bytes(None, b"<html><body></body></html>")
    validate_upload_magic_bytes("image/png", BENIGN_PNG_HEADER)


def test_validate_upload_size_rejects_oversized():
    with pytest.raises(AvatarValidationError):
        validate_upload_size(b"x" * (max_file_size() + 1))
    validate_upload_size(b"x" * max_file_size())


def test_max_file_size_and_allowed_types_defaults():
    assert max_file_size() == 5 * 1024 * 1024
    assert "image/png" in allowed_types()
    assert "image/jpeg" in allowed_types()
    assert "image/svg+xml" not in allowed_types()


# ---------------------------------------------------------------------------
# Security hardening
# ---------------------------------------------------------------------------


def test_is_serve_safe_blocks_script_svg():
    assert is_serve_safe("image/svg+xml", MALICIOUS_SVG) is False
    assert is_serve_safe("image/png", MALICIOUS_SVG) is False
    assert is_serve_safe("image/svg+xml", b"<svg><script>x</script></svg>") is False


def test_is_serve_safe_allows_benign_raster():
    assert is_serve_safe("image/png", BENIGN_PNG_HEADER) is True
    assert is_serve_safe("image/jpeg", b"\xff\xd8\xff\xe0" + b"\x00" * 64) is True


def test_is_serve_safe_fail_closed_on_blocked_type():
    assert is_serve_safe("text/html", b"<html></html>") is False
    assert is_serve_safe("application/javascript", b"alert(1)") is False


def test_parse_server_id_rejects_traversal_and_invalid():
    assert parse_server_id("9") == 9
    assert parse_server_id("42") == 42
    with pytest.raises(AvatarValidationError):
        parse_server_id("../..")
    with pytest.raises(AvatarValidationError):
        parse_server_id("..")
    with pytest.raises(AvatarValidationError):
        parse_server_id("abc")
    with pytest.raises(AvatarValidationError):
        parse_server_id("-1")
    with pytest.raises(AvatarValidationError):
        parse_server_id("9/../1")


def test_sanitize_filename_strips_traversal():
    assert sanitize_filename("../../etc/passwd") == "unnamed"
    assert sanitize_filename("foo/../../bar") == "unnamed"
    assert sanitize_filename("avatar/../icon.png") == "unnamed"
    assert sanitize_filename("my/avatar.png") == "my_avatar.png"
    assert sanitize_filename("avatar.png") == "avatar.png"
    assert sanitize_filename(None) == "unnamed"
    assert sanitize_filename("") == "unnamed"


def test_is_safe_avatar_identifier():
    assert is_safe_avatar_identifier("9") is True
    assert is_safe_avatar_identifier("42") is True
    assert is_safe_avatar_identifier("../..") is False
    assert is_safe_avatar_identifier("..") is False


# ---------------------------------------------------------------------------
# Route-level hardening (endpoints reject bad uploads before the manager)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_my_avatar_rejects_oversized_file():
    from src.api.routes.avatars import upload_my_avatar

    oversized = b"x" * (max_file_size() + 1)
    fake_avatars = MagicMock()
    with patch("src.api.get_avatars", return_value=fake_avatars):
        with pytest.raises(Exception) as ei:
            await upload_my_avatar(
                _upload_file(oversized, "image/png"), SimpleNamespace(user_id=1)
            )
    # FastAPI wraps the handler exception; assert a 400 surfaced.
    from fastapi import HTTPException

    assert isinstance(ei.value, HTTPException)
    assert ei.value.status_code == 400


@pytest.mark.asyncio
async def test_upload_my_avatar_rejects_svg_smuggled_as_png():
    from src.api.routes.avatars import upload_my_avatar

    fake_avatars = MagicMock()
    with patch("src.api.get_avatars", return_value=fake_avatars):
        with pytest.raises(Exception) as ei:
            await upload_my_avatar(
                _upload_file(MALICIOUS_SVG, "image/png"), SimpleNamespace(user_id=1)
            )
    from fastapi import HTTPException

    assert isinstance(ei.value, HTTPException)
    assert ei.value.status_code == 400
    # The manager must never be reached for a rejected payload.
    fake_avatars.upload_user_avatar.assert_not_called()


@pytest.mark.asyncio
async def test_upload_server_icon_rejects_bad_type():
    from src.api.routes.avatars import upload_server_icon

    fake_avatars = MagicMock()
    fake_servers = MagicMock()
    fake_servers.get_server.return_value = SimpleNamespace(owner_id=1)
    with (
        patch("src.api.get_avatars", return_value=fake_avatars),
        patch("src.api.get_servers", return_value=fake_servers),
    ):
        with pytest.raises(Exception) as ei:
            await upload_server_icon(
                "9",
                _upload_file(MALICIOUS_SVG, "image/png"),
                SimpleNamespace(user_id=1),
            )
    from fastapi import HTTPException

    assert isinstance(ei.value, HTTPException)
    assert ei.value.status_code == 400
    fake_avatars.upload_server_icon.assert_not_called()


# ---------------------------------------------------------------------------
# Internal helper behaviors
# ---------------------------------------------------------------------------


def test_checksum_cache_bounded():
    cache = _BoundedChecksumCache(maxsize=100, ttl=600.0)
    for i in range(250):
        _cache_set_checksum(cache, i, f"c{i}")
    assert len(cache) <= 100


def test_checksum_cache_lru_refresh():
    cache = _BoundedChecksumCache(maxsize=5, ttl=600.0)
    for i in range(5):
        cache[i] = f"c{i}"
    assert cache.get(0) == "c0"
    cache[5] = "c5"
    cache[6] = "c6"
    assert 0 in cache
    assert 1 not in cache
    assert 2 not in cache


def test_checksum_cache_ttl_expiry():
    cache = _BoundedChecksumCache(maxsize=100, ttl=0.05)
    cache[1] = "abc"
    assert cache.get(1) == "abc"
    import time

    time.sleep(0.08)
    assert cache.get(1) is None
    assert 1 not in cache


def test_cache_set_checksum_plain_dict_cap():
    cache: dict[int, str] = {}
    for i in range(_MAX_CHECKSUM_CACHE_ENTRIES + 50):
        _cache_set_checksum(cache, i, f"c{i}")
    assert len(cache) <= _MAX_CHECKSUM_CACHE_ENTRIES


def test_module_caches_are_bounded_type():
    assert isinstance(avatar_routes._avatar_checksum_cache, _BoundedChecksumCache)
    assert isinstance(avatar_routes._icon_checksum_cache, _BoundedChecksumCache)


def test_invalidation_helpers_clear_local_cache(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "src.core.database.invalidate_pattern", lambda pattern: calls.append(pattern)
    )

    avatar_routes._avatar_checksum_cache[42] = "old"
    avatar_routes._invalidate_user_avatar_cache(42)
    assert 42 not in avatar_routes._avatar_checksum_cache
    assert any("user_avatar_checksum:42" in c for c in calls)

    calls.clear()
    avatar_routes._icon_checksum_cache[7] = "old"
    avatar_routes._invalidate_server_icon_cache(7)
    assert 7 not in avatar_routes._icon_checksum_cache
    assert any("server_icon_checksum:7" in c for c in calls)


# ---------------------------------------------------------------------------
# Typed errors and models
# ---------------------------------------------------------------------------


def test_error_hierarchy():
    assert issubclass(AvatarValidationError, AvatarError)
    assert issubclass(AvatarNotFoundError, AvatarError)
    assert issubclass(AvatarStorageError, AvatarError)
    assert issubclass(AvatarUnauthorizedError, AvatarError)
    assert issubclass(AvatarForbiddenError, AvatarError)


def test_metadata_from_manager_roundtrip():
    result = {
        "url": "/api/v1/avatars/users/1",
        "width": 16,
        "height": 16,
        "size": 100,
        "content_type": "image/png",
        "animated": False,
        "checksum": "deadbeef",
    }
    meta = metadata_from_manager(result)
    assert meta.width == 16
    assert meta.height == 16
    assert meta.size == 100
    assert meta.content_type == "image/png"
    assert meta.animated is False
    assert meta.checksum == "deadbeef"
    assert meta.url == result["url"]


def test_upload_result_from_manager():
    result = {
        "url": "/api/v1/avatars/users/1",
        "width": 16,
        "height": 16,
        "size": 100,
        "animated": False,
    }
    up = upload_result_from_manager(result)
    assert up.success is True
    assert up.url == "/api/v1/avatars/users/1"
    assert up.width == 16
    assert up.height == 16
    assert up.size == 100
    assert up.animated is False
