"""Regression tests for the endpoint-level raw body cap.

``request.body()`` buffers the whole body before returning, so a check on
``len(body)`` cannot protect memory when the request has no trustworthy
``Content-Length`` (chunked transfer encoding, or a lying header). These tests
pin the guard to incremental streaming: it must stop reading as soon as the
running total would cross the cap, and it must still cache the bytes it read
so a route that parses the body afterwards sees them.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Dict, List, Optional

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.request_guards import read_body_capped

LIMIT = 1024
CHUNK = 256


class _StreamingRequest:
    """Request double exposing only ``stream()`` (no pre-read ``_body``)."""

    def __init__(
        self,
        chunks: List[bytes],
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self._chunks = chunks
        self.headers = headers or {}
        self.consumed: List[bytes] = []
        self._body: Optional[bytes] = None

    async def stream(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            self.consumed.append(chunk)
            yield chunk

    async def body(self) -> bytes:
        # Mirrors Starlette: the cache set by read_body_capped must be used.
        if self._body is None:
            self._body = b"".join([chunk async for chunk in self.stream()])
        return self._body


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _status(coro: Any) -> int:
    with pytest.raises(HTTPException) as exc:
        _run(coro)
    assert exc.value.status_code == 413
    return int(exc.value.status_code)


class TestStreamingBodyCap:
    def test_chunked_body_over_limit_stops_reading_at_the_cap(self) -> None:
        # 64 KiB available, but only five 256-byte chunks are needed to cross
        # the 1 KiB cap. The guard must never pull the remaining 59 chunks.
        chunks = [b"x" * CHUNK for _ in range(256)]
        request = _StreamingRequest(chunks)

        assert _status(read_body_capped(request, LIMIT)) == 413
        assert len(request.consumed) == LIMIT // CHUNK + 1
        assert len(request.consumed) < len(chunks)

    def test_chunked_body_exactly_at_limit_is_returned_and_cached(self) -> None:
        chunks = [b"x" * CHUNK for _ in range(LIMIT // CHUNK)]
        request = _StreamingRequest(chunks)

        body = _run(read_body_capped(request, LIMIT))
        assert body == b"x" * LIMIT
        assert len(request.consumed) == LIMIT // CHUNK
        # Downstream request.json()/body() must see the same bytes rather than
        # trying to consume the already-drained stream a second time.
        assert request._body == body
        assert _run(request.body()) == body

    def test_lying_content_length_still_caps_the_stream(self) -> None:
        chunks = [b"x" * CHUNK for _ in range(256)]
        request = _StreamingRequest(chunks, {"content-length": "10"})

        assert _status(read_body_capped(request, LIMIT)) == 413
        assert len(request.consumed) < len(chunks)

    def test_already_buffered_body_is_capped_without_re_streaming(self) -> None:
        request = _StreamingRequest([])
        request._body = b"x" * (LIMIT + 1)

        assert _status(read_body_capped(request, LIMIT)) == 413
        assert request.consumed == []


def _asgi_receive(chunks: List[bytes], seen: List[bytes]) -> Any:
    iterator = iter(chunks)

    async def receive() -> dict:
        try:
            body = next(iterator)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        seen.append(body)
        return {"type": "http.request", "body": body, "more_body": True}

    return receive


def test_real_starlette_request_can_be_parsed_after_capped_read() -> None:
    """A dependency caps the body; the route's ``request.json()`` must work."""
    payload = b'{"event": "ping"}'
    split = len(payload) // 2
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "headers": [(b"content-type", b"application/json")],
        },
        _asgi_receive([payload[:split], payload[split:]], []),
    )
    assert _run(read_body_capped(request, LIMIT)) == payload
    assert _run(request.json()) == {"event": "ping"}


def test_real_starlette_request_stops_pulling_chunks_at_the_cap() -> None:
    seen: List[bytes] = []
    chunks = [b"x" * CHUNK for _ in range(64)]
    request = Request(
        {"type": "http", "method": "POST", "headers": []},
        _asgi_receive(chunks, seen),
    )
    assert _status(read_body_capped(request, LIMIT)) == 413
    assert len(seen) == LIMIT // CHUNK + 1
    assert len(seen) < len(chunks)
