"""Regression guards for the two hot media-serving routes.

Locks in the behaviour of the resolved-body caches added to
``GET /api/v1/media/attachments/{filename}`` and
``GET /api/v1/avatars/servers/{server_id}``:

* a warm hit returns *identical bytes and headers* to a cold read;
* the warm hit skips the expensive storage / DB lookup (the perf win);
* every security gate still runs on the warm path -- path traversal is
  rejected, the per-file (IDOR) access check is never bypassed, the stream is
  range/bounded, and ``X-Content-Type-Options: nosniff`` is always present.
"""

from __future__ import annotations

import asyncio
import io
import uuid
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit

import pytest
import utils.config as config
from fastapi import HTTPException
from starlette.requests import Request

import src.api as api
from src.api import media_serving
from src.api.routes.avatars import routes as avatar_routes
from src.api.routes.avatars.storage import _icon_payload_cache
from src.core.auth.models import AccountType, TokenInfo
from src.utils import encryption

ATTACHMENTS_URL = "/api/v1/media/attachments"


def _invalidate_config_cache(instance) -> None:
    getattr(instance, "_invalidate_cache")()


@pytest.fixture
def attachment_media_dir(tmp_path):
    """Point the media module at a real local-filesystem upload directory."""
    instance = config._config_instance
    assert instance is not None
    original = instance.config.get("media")
    media_cfg = dict(original or {})
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
        if original is None:
            instance.config.pop("media", None)
        else:
            instance.config["media"] = original
        _invalidate_config_cache(instance)
        # clear() keeps LRUCache hit/miss counters; reset them too so this
        # module cannot pollute stats assertions in other test modules.
        for cache in (
            media_serving._media_body_cache,
            media_serving._media_metadata_cache,
            media_serving._media_response_meta_cache,
        ):
            cache.clear()
            cache.reset_stats()


def _request(
    query: bytes = b"", headers=None, path: str = "/api/v1/media/attachments/f"
):
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "raw_path": path.encode(),
            "query_string": query,
            "headers": headers or [(b"host", b"testserver")],
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("127.0.0.1", 0),
            "state": {},
            "root_path": "",
            "http_version": "1.1",
        }
    )


def _attachment_endpoint(app):
    for route in app.routes:
        path = getattr(route, "path", "") or ""
        if "media/attachments" in path and hasattr(route, "endpoint"):
            return route.endpoint
    raise AssertionError("attachment endpoint not registered")


def _png_payload() -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), color=(9, 8, 7)).save(buffer, format="PNG")
    return buffer.getvalue()


class _CountingMedia:
    """Delegating proxy that counts the two hot-path module calls."""

    def __init__(self, real):
        self._real = real
        self.storage_reads = 0
        self.access_checks = 0
        self.deny_access = False

    def __getattr__(self, name):
        return getattr(self._real, name)

    def get_file_stream_optimized(self, *args, **kwargs):
        self.storage_reads += 1
        return self._real.get_file_stream_optimized(*args, **kwargs)

    def check_file_access(self, *args, **kwargs):
        self.access_checks += 1
        if self.deny_access:
            return False
        return self._real.check_file_access(*args, **kwargs)


def _prepare_attachment(media_manager, auth_manager, db):
    unique = uuid.uuid4().hex[:8]
    username = f"hotroute_{unique}"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=f"{username}@example.com",
            password="TestPass123!",
        )
    payload = _png_payload()
    result = media_manager.upload_file(
        user_id=user.id,
        file_data=payload,
        filename="hot.png",
        content_type="image/png",
    )
    db.execute(
        "UPDATE media_files SET scan_status = 'clean' WHERE id = ?",
        (result.file_id,),
    )
    row = db.fetch_one(
        "SELECT filename FROM media_files WHERE id = ?", (result.file_id,)
    )
    stored = row["filename"]
    signed = media_manager.sign_url(
        result.file_id, url=f"{ATTACHMENTS_URL}/{stored}", expires_in=600
    )
    return stored, payload, signed.url, user.id


def _call(endpoint, filename, url):
    parts = urlsplit(url)
    req = _request(query=parts.query.encode(), path=parts.path)
    return asyncio.run(endpoint(filename, req))


# --- Attachment route -------------------------------------------------------


@pytest.mark.asyncio
async def test_attachment_warm_hit_is_identical_and_skips_storage(
    attachment_media_dir, test_client, db, media_manager, auth_manager
):
    stored, payload, signed_url, _ = _prepare_attachment(
        media_manager, auth_manager, db
    )
    endpoint = _attachment_endpoint(test_client.app)
    counting = _CountingMedia(api.get_media())
    media_serving._media_body_cache.clear()

    with patch.object(api, "get_media", return_value=counting):
        cold = await endpoint(
            stored,
            _request(
                query=urlsplit(signed_url).query.encode(),
                path=urlsplit(signed_url).path,
            ),
        )
        warm = await endpoint(
            stored,
            _request(
                query=urlsplit(signed_url).query.encode(),
                path=urlsplit(signed_url).path,
            ),
        )

    assert cold.status_code == 200
    assert warm.status_code == 200
    assert cold.body_iterator and warm.body_iterator
    # Cold read hits storage exactly once; the warm hit never touches it.
    assert counting.storage_reads == 1, counting.storage_reads
    assert media_serving._media_body_cache.stats()["hits"] >= 1

    # Identical bytes + security headers on both paths.
    assert await _collect(cold.body_iterator) == [payload]
    assert await _collect(warm.body_iterator) == [payload]
    assert warm.headers["x-content-type-options"] == "nosniff"
    assert warm.headers["content-disposition"] == "inline"
    assert warm.headers["etag"] == cold.headers["etag"]


async def _collect(iterator):
    out = []
    async for chunk in iterator:
        out.append(bytes(chunk))
    return out


def test_attachment_range_on_warm_hit_is_bounded(
    attachment_media_dir, test_client, db, media_manager, auth_manager
):
    stored, payload, signed_url, _ = _prepare_attachment(
        media_manager, auth_manager, db
    )
    endpoint = _attachment_endpoint(test_client.app)

    # Warm the body cache.
    _call(endpoint, stored, signed_url)

    parts = urlsplit(signed_url)
    req = _request(
        query=parts.query.encode(),
        path=parts.path,
        headers=[(b"host", b"testserver"), (b"range", b"bytes=0-7")],
    )
    resp = asyncio.run(endpoint(stored, req))
    assert resp.status_code == 206
    assert resp.headers["content-range"] == f"bytes 0-7/{len(payload)}"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert asyncio.run(_collect(resp.body_iterator)) == [payload[:8]]


@pytest.mark.parametrize(
    "malicious",
    ["..", "../secret.png", "a/b.png", "a\\b.png", "a\x00b.png", "....//x"],
)
def test_attachment_path_traversal_rejected_before_any_storage(
    attachment_media_dir, test_client, db, media_manager, auth_manager, malicious
):
    endpoint = _attachment_endpoint(test_client.app)
    counting = _CountingMedia(api.get_media())
    with patch.object(api, "get_media", return_value=counting):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(endpoint(malicious, _request()))
    assert exc.value.status_code == 400
    assert counting.storage_reads == 0
    assert counting.access_checks == 0


def test_attachment_warm_body_cache_never_bypasses_access_check(
    attachment_media_dir, test_client, db, media_manager, auth_manager
):
    stored, _payload, signed_url, user_id = _prepare_attachment(
        media_manager, auth_manager, db
    )
    endpoint = _attachment_endpoint(test_client.app)
    counting = _CountingMedia(api.get_media())

    # Populate the body cache through a legitimate signed request.
    with patch.object(api, "get_media", return_value=counting):
        warm = _call(endpoint, stored, signed_url)
    assert warm.status_code == 200

    # Now the same (cached) file requested with an ordinary user token whose
    # per-file access is denied. The IDOR check MUST still run and deny.
    counting.deny_access = True
    token_info = TokenInfo(
        valid=True,
        token_type="user",
        account_id=user_id,
        user_id=user_id,
        session_id=None,
        permissions={},
        rate_limit_tier="default",
        expires_at=None,
        username="denied",
        account_type=AccountType.USER,
    )
    fake_auth = SimpleNamespace(verify_token=lambda *a, **k: token_info)
    headers = [(b"host", b"testserver"), (b"authorization", b"Bearer faketoken")]
    req = _request(path=f"{ATTACHMENTS_URL}/{stored}", headers=headers)
    with (
        patch.object(api, "get_media", return_value=counting),
        patch.object(api, "get_auth", return_value=fake_auth),
        patch.object(api, "get_admin", return_value=None),
        patch("src.api.middleware.authentication.get_token_info", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(endpoint(stored, req))
    assert exc.value.status_code == 403
    assert counting.access_checks >= 1  # ran even though the body was cached


# --- Server icon route ------------------------------------------------------


def _insert_icon(db, sid: int, payload: bytes, checksum: str = "cksum") -> None:
    db.execute(
        "INSERT INTO server_icons (id, server_id, icon_data, content_type, "
        "width, height, size, checksum, animated, uploaded_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (9000 + sid, sid, payload, "image/png", 8, 8, len(payload), checksum, 0, 1),
    )


@pytest.mark.asyncio
async def test_server_icon_warm_hit_is_identical_and_skips_lookup(test_client, db):
    sid = 770001
    payload = b"\x89PNG\r\n\x1a\n" + b"z" * 128
    _insert_icon(db, sid, payload)
    avatars = api.get_avatars()
    calls = {"n": 0}
    original = avatars.get_server_icon_data

    def counting(*a, **k):
        calls["n"] += 1
        return original(*a, **k)

    _icon_payload_cache.clear()
    req = _request(path=f"/api/v1/avatars/servers/{sid}")
    with patch.object(avatars, "get_server_icon_data", counting):
        cold = await avatar_routes.get_server_icon(sid, req)
        warm = await avatar_routes.get_server_icon(sid, req)

    assert cold.status_code == 200
    # The manager's binary lookup ran only once (cold); the warm hit reused it.
    assert calls["n"] == 1
    assert _icon_payload_cache.stats()["hits"] >= 1
    assert cold.body == warm.body == payload
    assert warm.headers["x-content-type-options"] == "nosniff"
    assert warm.headers["etag"] == cold.headers["etag"]
    assert warm.headers["content-disposition"] == 'inline; filename="icon.png"'


def test_server_icon_invalidation_drops_resolved_payload(test_client, db):
    sid = 770002
    payload = b"\x89PNG\r\n\x1a\n" + b"q" * 64
    _insert_icon(db, sid, payload)
    avatars = api.get_avatars()
    calls = {"n": 0}
    original = avatars.get_server_icon_data

    def counting(*a, **k):
        calls["n"] += 1
        return original(*a, **k)

    _icon_payload_cache.clear()
    req = _request(path=f"/api/v1/avatars/servers/{sid}")
    with patch.object(avatars, "get_server_icon_data", counting):
        asyncio.run(avatar_routes.get_server_icon(sid, req))
        assert calls["n"] == 1
        asyncio.run(avatar_routes.get_server_icon(sid, req))
        assert calls["n"] == 1  # warm hit

        # An upload/delete invalidates the resolved payload.
        avatar_routes._invalidate_server_icon_cache(sid)
        assert sid not in _icon_payload_cache
        asyncio.run(avatar_routes.get_server_icon(sid, req))
        assert calls["n"] == 2  # re-resolved after invalidation


def test_unsafe_server_icon_payload_is_never_cached(test_client, db):
    sid = 770003
    malicious = (
        b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)">'
        b"<script>alert(document.cookie)</script></svg>"
    )
    _insert_icon(db, sid, malicious)
    _icon_payload_cache.clear()
    req = _request(path=f"/api/v1/avatars/servers/{sid}")
    resp = asyncio.run(avatar_routes.get_server_icon(sid, req))

    assert resp.status_code == 200
    # A script-carrying stored icon must fall back to the safe generated SVG.
    assert b"<script" not in resp.body
    assert sid not in _icon_payload_cache
    assert resp.headers["x-content-type-options"] == "nosniff"
