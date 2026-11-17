"""Security invariants for every media / artifact GET surface.

Two properties must hold on every read path that can hand bytes back to a
browser:

1. **Authentication / authorization** is mandatory unless an explicit signed
   URL grants access — an anonymous caller never receives a private body, and
   artifact reads always run the ``get_current_user`` dependency plus a scope
   check before streaming.
2. **``X-Content-Type-Options: nosniff``** is pinned on every 200 response so a
   mis-declared HTML/SVG payload cannot be sniffed and rendered under our
   origin.

The media attachment checks invoke the registered endpoint directly with
crafted requests and mocked module lookups; artifact and avatar checks call
their endpoints against deterministic mocks. No full-app boot, network, sleeps
or wall-clock dependence.
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

import src.api.routes.artifacts.routes.exports as exports
import src.api.media_serving as media_serving
from src.api.middleware.authentication import get_current_user

ATTACHMENTS_URL = "/api/v1/media/attachments"

# Smallest credible 1x1 PNG; content is irrelevant to the serving gate.
MINI_PNG = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _attachment_endpoint():
    """Register the attachment route on a minimal app and return its callable."""
    app = FastAPI()
    media_serving.register_attachment_route(app)
    return next(
        r.endpoint
        for r in app.routes
        if getattr(r, "path", "").endswith("/media/attachments/{filename:path}")
    )


def _request(*, authorization=None, cookie=None, signed_uid=None, range_header=None):
    headers = {}
    if authorization:
        headers["Authorization"] = authorization
    if range_header:
        headers["Range"] = range_header
    query_params = {}
    if signed_uid is not None:
        query_params["uid"] = str(signed_uid)
    return SimpleNamespace(
        url=SimpleNamespace(path=f"{ATTACHMENTS_URL}/serve.png", query=""),
        headers=headers,
        cookies={"plexichat_token": cookie} if cookie else {},
        query_params=query_params,
        scope={},
    )


# ---------------------------------------------------------------------------
# Media attachment GET
# ---------------------------------------------------------------------------


def test_attachment_get_requires_authentication():
    """Without a signature or token the endpoint refuses before any lookup."""
    media = MagicMock()
    media.verify_signed_url.return_value = (False, 0)
    endpoint = _attachment_endpoint()
    with patch("src.api.get_media", return_value=media):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(endpoint("serve.png", _request()))
    assert exc.value.status_code == 401


def test_attachment_get_rejects_multi_segment_filename():
    """A filename that is not a single path segment is refused up front."""
    endpoint = _attachment_endpoint()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(endpoint("nested/file.png", _request()))
    assert exc.value.status_code == 400


def test_authenticated_attachment_get_pins_nosniff():
    """A validly signed GET carries nosniff + a safe Content-Disposition."""
    media_serving._media_metadata_cache.clear()
    media = MagicMock()
    media.verify_signed_url.return_value = (True, 0)
    media.get_file_stream_optimized.return_value = (
        io.BytesIO(MINI_PNG),
        len(MINI_PNG),
        "image/png",
    )
    db = MagicMock()
    db.fetch_one.side_effect = [
        {
            "id": 1,
            "storage_backend": "local",
            "storage_path": "serve.png",
            "content_type": "image/png",
            "original_filename": "serve.png",
        },
        {"scan_status": "clean", "deleted": 0},
    ]
    endpoint = _attachment_endpoint()
    with (
        patch("src.api.get_media", return_value=media),
        patch("src.api.get_db", return_value=db),
    ):
        response = asyncio.run(endpoint("serve.png", _request()))

    assert isinstance(response, StreamingResponse)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-disposition" in response.headers
    media_serving._media_metadata_cache.clear()


# ---------------------------------------------------------------------------
# Artifact export GET
# ---------------------------------------------------------------------------


def test_export_route_requires_current_user_dependency():
    """The export GET cannot be registered without the auth dependency."""
    export_route = next(r for r in exports.router.routes if r.path.endswith("/export"))
    dep_calls = [dep.call for dep in export_route.dependant.dependencies]
    assert get_current_user in dep_calls


class _Artifact:
    id = 123
    conversation_id = 456
    server_id = None
    author_id = 789
    title = "doc"
    summary = "sum"
    artifact_type = SimpleNamespace(value="plexiscript")
    payload = {"content": "private-content"}


class _Manager:
    def get(self, aid):
        return _Artifact()


def _patch_exports(monkeypatch, authorize=None):
    monkeypatch.setattr(exports, "_get_manager", lambda: _Manager())
    monkeypatch.setattr(exports, "_require_artifact_capability", lambda *_: None)
    monkeypatch.setattr(
        exports,
        "_authorize_scope",
        authorize or (lambda *a, **k: None),
    )


def test_export_get_pins_nosniff_and_attachment_disposition(monkeypatch):
    _patch_exports(monkeypatch)
    response = asyncio.run(
        exports._export_artifact_impl(
            "123", export_format="txt", current_user=SimpleNamespace(user_id=789)
        )
    )
    assert isinstance(response, StreamingResponse)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]


def test_export_get_denies_out_of_scope_reader(monkeypatch):
    def deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="denied")

    _patch_exports(monkeypatch, authorize=deny)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            exports._export_artifact_impl(
                "123", export_format="txt", current_user=SimpleNamespace(user_id=1)
            )
        )
    assert exc.value.status_code == 403


# ---------------------------------------------------------------------------
# Avatar GET (a deliberately public GET that still must not allow sniffing)
# ---------------------------------------------------------------------------


def test_avatar_get_pins_nosniff_and_safe_disposition():
    import src.api.routes.avatars.routes as avatar_routes

    # Force the real-avatar response path rather than a cached default SVG.
    avatar_routes._avatar_checksum_cache.clear()
    avatar_routes._avatar_absent_cache.clear()

    fake_avatars = MagicMock()
    fake_avatars.get_user_avatar_checksum.return_value = "deadbeef"
    fake_avatars.get_user_avatar_data.return_value = (MINI_PNG, "image/png", "deadbeef")

    request = MagicMock()
    request.headers = {}
    with patch("src.api.get_avatars", return_value=fake_avatars):
        response = asyncio.run(avatar_routes.get_user_avatar(1, request))

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"] == 'inline; filename="avatar.png"'
