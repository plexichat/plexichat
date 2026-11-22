"""Regression tests for the admin PlexiJoin connection-create body cap.

``Request.json()`` buffers the entire request body before returning, so a
caller holding an admin token could make the worker allocate an arbitrary
amount of memory with one POST to ``/admin/plexijoin/connections``. The
client-facing ``/Join/request`` route already reads through ``read_body_capped``;
the admin route was the one mutating PlexiJoin boundary still doing an
unbounded ``await request.json()``.

These tests pin three properties: a lying/oversized ``Content-Length`` is
refused up front, a chunked body is stopped incrementally at the cap, and a
normal body still parses and reaches the manager unchanged.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from typing import Any, List
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.routes.admin import plexijoin as route

pytestmark = pytest.mark.unit

VALID_BODY = {
    "remote_instance_id": "remote-1",
    "remote_url": "https://remote.example.com",
    "shared_key": "a-strong-shared-key-value-1234567890",
    "note": "hello",
}


class _FakeManager:
    def __init__(self) -> None:
        self.calls: List[dict] = []

    def create_connection(self, **kwargs: Any) -> dict:
        self.calls.append(kwargs)
        return {"id": 4242}


def _request(
    body: bytes, *, content_length: int | None = None, seen: List[bytes] | None = None
) -> Request:
    """Build a real Starlette Request whose stream yields ``body`` in chunks."""
    headers = [(b"content-type", b"application/json")]
    if content_length is not None:
        headers.append((b"content-length", str(content_length).encode()))
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/admin/plexijoin/connections",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
    }
    emitted = seen if seen is not None else []
    chunk_size = 1024
    offset = 0

    async def receive() -> dict:
        nonlocal offset
        if offset >= len(body):
            return {"type": "http.request", "body": b"", "more_body": False}
        chunk = body[offset : offset + chunk_size]
        offset += len(chunk)
        emitted.append(chunk)
        return {"type": "http.request", "body": chunk, "more_body": True}

    return Request(scope, receive)


@contextmanager
def _patched(manager: _FakeManager):
    """Bypass auth/licence/DB so the body boundary is the only thing under test."""
    patches = [
        patch.object(route, "check_host_restriction"),
        patch.object(route, "require_admin_permission", return_value=1),
        patch.object(route, "_get_plexijoin_manager", return_value=manager),
    ]
    for p in patches:
        p.start()
    try:
        yield
    finally:
        for p in reversed(patches):
            p.stop()


def _call(request: Request) -> dict:
    return asyncio.run(route.create_connection(request))


def _status(coro: Any) -> int:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(coro)
    return int(exc.value.status_code)


class TestAdminConnectionBodyCap:
    def test_oversized_content_length_is_refused_before_manager(self) -> None:
        manager = _FakeManager()
        huge = b"x" * (route.MAX_ADMIN_JOIN_BODY + 1)
        request = _request(huge, content_length=len(huge))

        with _patched(manager):
            assert _status(route.create_connection(request)) == 413
        assert manager.calls == []

    def test_chunked_body_is_bounded_incrementally(self) -> None:
        manager = _FakeManager()
        # No Content-Length: the guard must stop pulling chunks once the
        # running total would cross the cap, not buffer the whole 512 KiB.
        huge = b"x" * (8 * route.MAX_ADMIN_JOIN_BODY)
        seen: List[bytes] = []
        request = _request(huge, seen=seen)

        with _patched(manager):
            assert _status(route.create_connection(request)) == 413
        assert manager.calls == []
        assert sum(len(chunk) for chunk in seen) <= route.MAX_ADMIN_JOIN_BODY + 1024
        # 512 KiB of body would be 512 chunks if it were buffered in full.
        assert len(seen) < 512

    def test_normal_body_is_parsed_and_reaches_manager(self) -> None:
        manager = _FakeManager()
        request = _request(json.dumps(VALID_BODY).encode("utf-8"))

        with _patched(manager):
            assert _call(request)["connection_id"] == 4242
        assert len(manager.calls) == 1
        assert (
            manager.calls[0]["remote_instance_id"] == VALID_BODY["remote_instance_id"]
        )
        assert manager.calls[0]["remote_url"] == VALID_BODY["remote_url"]
        assert manager.calls[0]["note"] == VALID_BODY["note"]

    def test_non_object_body_is_a_clean_400(self) -> None:
        manager = _FakeManager()
        request = _request(b'["not", "an", "object"]')

        with _patched(manager):
            assert _status(route.create_connection(request)) == 400
        assert manager.calls == []

    def test_malformed_json_is_a_clean_400(self) -> None:
        manager = _FakeManager()
        request = _request(b"{not json")

        with _patched(manager):
            assert _status(route.create_connection(request)) == 400
        assert manager.calls == []
