"""Performance regressions for the media-serving request paths.

These pin the *cost* properties added to the proxy and attachment routes:
the proxy rate-limit policy is parsed once per config version, the cheap
URL-scheme verdict is memoised by URL, and the attachment route freezes its
CORS policy and caches derived per-file response metadata. They also assert
the security verdicts (SSRF scheme gate, inline-safety allow-list, bounded
read size) are unchanged by the caching.
"""

from __future__ import annotations

import urllib.parse
import uuid
from unittest.mock import patch

import pytest

import utils.config as config
from src.api import media_serving
from src.api.routes import media as media_routes
from src.core.media._streaming import DEFAULT_CHUNK_SIZE, bounded_chunk_size
from src.utils import encryption

ATTACHMENTS_URL = "/api/v1/media/attachments"


def _invalidate_config_cache(instance: object) -> None:
    # The shipped utils/config.pyi stub omits this private ConfigLoader method.
    getattr(instance, "_invalidate_cache")()


# === Proxy request policy ===


def test_proxy_policy_is_cached_and_tracks_config_edits():
    """The rate-limit ints are parsed once per version, not on every request."""
    instance = config._config_instance
    assert instance is not None
    original_media = instance.config.get("media")
    media_routes._proxy_policy_snapshot.invalidate()
    try:
        assert media_routes._proxy_rate_limit_params() == (120, 60)
        assert media_routes._proxy_rate_limit_params() == (120, 60)
        assert media_routes._proxy_policy_snapshot.stats()["rebuilds"] == 1

        # An in-place config edit (the pattern used by tests/admin reloads)
        # changes the version token and must be observed on the next access.
        media_cfg = dict(original_media or {})
        media_cfg["proxy_rate_limit"] = {
            "requests_per_window": "7",
            "window_seconds": "9",
        }
        instance.config["media"] = media_cfg
        _invalidate_config_cache(instance)
        assert media_routes._proxy_rate_limit_params() == (7, 9)
        assert media_routes._proxy_policy_snapshot.stats()["rebuilds"] == 2
    finally:
        if original_media is None:
            instance.config.pop("media", None)
        else:
            instance.config["media"] = original_media
        _invalidate_config_cache(instance)
        media_routes._proxy_policy_snapshot.invalidate()


def test_proxy_url_verdict_is_memoised_by_exact_url():
    cache = media_routes._proxy_url_verdict_cache
    cache.clear()
    try:
        with patch("urllib.parse.urlparse", wraps=urllib.parse.urlparse) as parse:
            assert media_routes._is_allowed_proxy_url("https://example.com/a.png")
            assert media_routes._is_allowed_proxy_url("https://example.com/a.png")
            assert parse.call_count == 1

            assert not media_routes._is_allowed_proxy_url("javascript:alert(1)")
            assert not media_routes._is_allowed_proxy_url("javascript:alert(1)")
            assert parse.call_count == 2

        assert cache.stats()["hits"] == 2
        assert len(cache) == 2
    finally:
        cache.clear()


def test_proxy_route_still_rejects_unsafe_scheme(test_client, auth_manager):
    """The cached verdict must not open the route-level SSRF early gate."""
    unique = uuid.uuid4().hex[:8]
    username = f"proxyperf_{unique}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    with patch.object(encryption, "verify_password", return_value=True):
        token = auth_manager.login(username, "TestPass123!").token

    resp = test_client.get(
        "/api/v1/media/proxy?url=file%3A%2F%2F%2Fetc%2Fpasswd",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


# === Attachment response metadata ===


def test_cors_headers_preserve_exact_behavior():
    allowed = frozenset({"https://app.example"})
    assert media_serving._cors_headers("https://app.example", allowed, False) == {
        "Access-Control-Allow-Origin": "https://app.example",
        "Access-Control-Allow-Credentials": "true",
    }
    # Unlisted origins still fail closed under an explicit-origin policy.
    assert media_serving._cors_headers("https://evil.example", allowed, False) == {}
    # Wildcard echoes the origin with credentials when one is present...
    assert media_serving._cors_headers("https://evil.example", allowed, True) == {
        "Access-Control-Allow-Origin": "https://evil.example",
        "Access-Control-Allow-Credentials": "true",
    }
    # ...and sends a bare '*' when the request has no Origin header.
    assert media_serving._cors_headers(None, allowed, True) == {
        "Access-Control-Allow-Origin": "*"
    }
    assert media_serving._cors_headers(None, allowed, False) == {}


def test_response_metadata_is_memoised_and_sanitised():
    cache = media_serving._media_response_meta_cache
    cache.clear()
    try:
        with patch.object(
            media_serving,
            "sanitize_header_filename",
            wraps=media_serving.sanitize_header_filename,
        ) as sanitize:
            first = media_serving._response_metadata(
                "stored.png", "résumé\u202efinal.png", "image/png"
            )
            second = media_serving._response_metadata(
                "stored.png", "résumé\u202efinal.png", "image/png"
            )
        assert first == second
        assert sanitize.call_count == 1
        assert cache.stats()["hits"] == 1
        # NFKD + ASCII folding drops the combining accents and the bidi
        # control character; the result is a safe header filename.
        assert first[0] == "resumefinal.png"
        assert first[1] is True
        assert first[2] == 'attachment; filename="resumefinal.png"'
    finally:
        cache.clear()


@pytest.mark.parametrize(
    "content_type,inline_safe",
    [
        ("image/png", True),
        ("IMAGE/WEBP", True),
        ("video/mp4", True),
        ("audio/ogg", True),
        ("image/svg+xml", False),
        ("image/svg", False),
        ("text/html", False),
        ("application/octet-stream", False),
    ],
)
def test_inline_content_type_verdict_is_unchanged(content_type, inline_safe):
    media_serving._media_response_meta_cache.clear()
    meta = media_serving._response_metadata("f", "f.txt", content_type)
    assert meta[1] is inline_safe


# === Streaming bounded-size fast path ===


def test_default_chunk_size_skips_validation_but_explicit_sizes_are_still_checked():
    with patch("src.core.media._streaming.bounded_int", return_value=8192) as bounded:
        assert bounded_chunk_size() == DEFAULT_CHUNK_SIZE
        bounded.assert_not_called()
        assert bounded_chunk_size(8192) == 8192
        bounded.assert_called_once()


# === End-to-end attachment headers ===


@pytest.fixture
def attachment_media_dir(tmp_path):
    instance = config._config_instance
    assert instance is not None
    original_media = instance.config.get("media")
    media_cfg = dict(original_media or {})
    media_cfg.update(
        {
            "storage_backend": "local",
            "local_path": str(tmp_path / "uploads"),
            "local_url": "/media",
            "compression": {"enabled": False},
            "signing_key": "perf-test-signing-key",
            "signing_expiry": 3600,
            "scanner_enabled": False,
            "proxy_enabled": False,
        }
    )
    instance.config["media"] = media_cfg
    _invalidate_config_cache(instance)
    try:
        yield tmp_path / "uploads"
    finally:
        if original_media is None:
            instance.config.pop("media", None)
        else:
            instance.config["media"] = original_media
        _invalidate_config_cache(instance)


def test_attachment_headers_and_range_are_preserved(
    attachment_media_dir, test_client, db, media_manager, auth_manager
):
    unique = uuid.uuid4().hex[:8]
    username = f"attachperf_{unique}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    Image = pytest.importorskip("PIL.Image")
    import io

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color=(1, 2, 3)).save(buffer, format="PNG")
    payload = buffer.getvalue()

    result = media_manager.upload_file(
        user_id=user.id,
        file_data=payload,
        filename="test_file.png",
        content_type="image/png",
    )
    db.execute(
        "UPDATE media_files SET scan_status = 'clean' WHERE id = ?",
        (result.file_id,),
    )
    row = db.fetch_one(
        "SELECT filename FROM media_files WHERE id = ?", (result.file_id,)
    )

    signed = media_manager.sign_url(
        result.file_id, url=f"{ATTACHMENTS_URL}/{row['filename']}", expires_in=600
    )
    resp = test_client.get(signed.url)
    assert resp.status_code == 200, resp.text
    assert resp.content == payload
    assert resp.headers["content-disposition"] == "inline"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert resp.headers["accept-ranges"] == "bytes"

    ranged = test_client.get(signed.url, headers={"Range": "bytes=0-7"})
    assert ranged.status_code == 206
    assert ranged.content == payload[:8]
    assert ranged.headers["content-range"] == f"bytes 0-7/{len(payload)}"
