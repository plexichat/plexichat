"""Artifact export streams in bounded chunks without skipping authorization."""

import asyncio
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from src.core.auth.models import TokenInfo
import src.api.routes.artifacts.routes.exports as exports

_PAYLOAD = "x" * (200 * 1024)  # > 3 chunks at 64 KiB


class _Artifact:
    id = 123
    conversation_id = 456
    server_id = None
    author_id = 789
    title = "doc"
    summary = "sum"
    artifact_type = SimpleNamespace(value="plexiscript")
    payload = {"content": _PAYLOAD}


class _Manager:
    def get(self, aid):
        return _Artifact()


def _patch(monkeypatch, authorize=None):
    monkeypatch.setattr(exports, "_get_manager", lambda: _Manager())
    monkeypatch.setattr(exports, "_require_artifact_capability", lambda *_: None)
    monkeypatch.setattr(
        exports,
        "_authorize_scope",
        authorize or (lambda *a, **k: None),
    )


async def _collect(response):
    chunks = []
    async for chunk in response.body_iterator:
        chunks.append(chunk)
    return chunks


def test_export_yields_bounded_chunks(monkeypatch):
    _patch(monkeypatch)

    response = asyncio.run(
        exports._export_artifact_impl(
            "123",
            export_format="txt",
            current_user=cast(TokenInfo, SimpleNamespace(user_id=1)),
        )
    )

    assert isinstance(response, StreamingResponse)
    chunks = asyncio.run(_collect(response))
    assert len(chunks) > 1
    assert all(len(chunk) <= exports._EXPORT_CHUNK_BYTES for chunk in chunks)
    assert b"".join(chunks) == _PAYLOAD.encode("utf-8")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "attachment" in response.headers["content-disposition"]


def test_export_still_authorizes_before_streaming(monkeypatch):
    def deny(*args, **kwargs):
        raise HTTPException(status_code=403, detail="denied")

    _patch(monkeypatch, authorize=deny)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            exports._export_artifact_impl(
                "123",
                export_format="txt",
                current_user=cast(TokenInfo, SimpleNamespace(user_id=1)),
            )
        )
    assert exc.value.status_code == 403
