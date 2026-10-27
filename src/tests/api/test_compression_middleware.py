"""Behavior of the gzip response compression middleware.

The middleware exists so API responses are compressed on deployments that do
not have nginx in front of them. These tests pin the decisions that make it
safe to enable everywhere: what gets compressed, and -- more importantly --
what must never be re-encoded (byte ranges, attachments, already-encoded
bodies, user media, event streams).
"""

import asyncio
import gzip
import json

import pytest
from fastapi import FastAPI, Response
from fastapi.responses import PlainTextResponse, StreamingResponse
from fastapi.testclient import TestClient

from src.api.middleware.compression import (
    CompressionMiddleware,
    is_compressible_type,
)

BIG = {"items": [f"message-{i}" for i in range(600)]}


def build_app(minimum_size: int = 1024) -> FastAPI:
    app = FastAPI()
    app.add_middleware(CompressionMiddleware, minimum_size=minimum_size)

    @app.get("/api/v1/big")
    def big() -> dict:
        return BIG

    @app.get("/api/v1/small")
    def small() -> dict:
        return {"ok": True}

    @app.get("/api/v1/text")
    def text() -> PlainTextResponse:
        return PlainTextResponse("x" * 4096)

    @app.get("/api/v1/stream")
    def stream() -> StreamingResponse:
        def chunks():
            for index in range(64):
                yield f"chunk-{index}-" + ("y" * 64) + "\n"

        return StreamingResponse(chunks(), media_type="text/plain")

    @app.get("/api/v1/attachment")
    def attachment() -> Response:
        return Response(
            content=b"z" * 4096,
            media_type="text/plain",
            headers={"Content-Disposition": 'attachment; filename="log.txt"'},
        )

    @app.get("/api/v1/already-encoded")
    def already_encoded() -> Response:
        return Response(
            content=b"pretend-brotli-bytes" * 100,
            media_type="application/json",
            headers={"Content-Encoding": "br"},
        )

    @app.get("/api/v1/partial")
    def partial() -> Response:
        return Response(
            content=b"r" * 4096,
            media_type="application/json",
            status_code=206,
        )

    @app.get("/api/v1/no-content")
    def no_content() -> Response:
        return Response(status_code=204)

    @app.get("/api/v1/image")
    def image() -> Response:
        return Response(content=b"\xff\xd8" + b"j" * 4096, media_type="image/jpeg")

    @app.get("/api/v1/events")
    def events() -> Response:
        return Response(
            content=b"data: " + b"e" * 4096 + b"\n\n",
            media_type="text/event-stream",
        )

    return app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(build_app())


def test_large_json_is_gzipped(client: TestClient):
    response = client.get("/api/v1/big", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
    # The length changes when the body is encoded, so it must not be forwarded.
    assert "content-length" not in response.headers
    assert "accept-encoding" in response.headers["vary"].lower()
    # httpx decodes on read; the payload has to survive the round trip.
    assert response.json() == BIG


def test_identity_when_client_does_not_accept_gzip(client: TestClient):
    response = client.get("/api/v1/big", headers={"Accept-Encoding": "identity"})

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert "content-length" in response.headers
    assert response.json() == BIG


def test_small_response_is_not_compressed(client: TestClient):
    response = client.get("/api/v1/small", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in response.headers
    assert response.json() == {"ok": True}


def test_streamed_response_is_compressed_and_reassembles(client: TestClient):
    response = client.get("/api/v1/stream", headers={"Accept-Encoding": "gzip"})

    assert response.headers["content-encoding"] == "gzip"
    body = response.text
    assert body.startswith("chunk-0-")
    assert body.rstrip().endswith("chunk-63-" + ("y" * 64))


def test_range_request_is_never_reencoded(client: TestClient):
    response = client.get(
        "/api/v1/big",
        headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"},
    )

    assert "content-encoding" not in response.headers


def test_partial_response_is_never_reencoded(client: TestClient):
    response = client.get("/api/v1/partial", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in response.headers
    assert response.headers["content-length"] == "4096"


def test_attachment_is_not_compressed(client: TestClient):
    response = client.get("/api/v1/attachment", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in response.headers


def test_body_encoded_by_another_layer_is_left_alone(client: TestClient):
    response = client.get(
        "/api/v1/already-encoded",
        headers={"Accept-Encoding": "gzip, br"},
    )

    # Our own encoding must not be stacked on top of the upstream one.
    assert response.headers["content-encoding"] == "br"


def test_no_content_response_has_no_encoding(client: TestClient):
    response = client.get("/api/v1/no-content", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 204
    assert "content-encoding" not in response.headers


def test_binary_media_is_not_compressed(client: TestClient):
    response = client.get("/api/v1/image", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in response.headers


def test_event_stream_is_not_compressed(client: TestClient):
    response = client.get("/api/v1/events", headers={"Accept-Encoding": "gzip"})

    assert "content-encoding" not in response.headers


def _http_scope(path: str = "/raw", accept_gzip: bool = True) -> dict:
    headers = [(b"host", b"testserver")]
    if accept_gzip:
        headers.append((b"accept-encoding", b"gzip"))
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }


async def _collect(app, scope: dict) -> list:
    """Drive an ASGI app directly so the raw (still encoded) bytes are visible."""
    messages: list = []
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message) -> None:
        messages.append(message)

    await app(scope, receive, send)
    return messages


def _body(messages: list) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )


def test_compressed_bytes_are_a_valid_gzip_stream():
    """The wire format, checked without any client-side decoding."""
    raw = b"".join(
        json.dumps({"index": index, "text": "a" * 200}).encode() for index in range(200)
    )
    assert len(raw) > 1024

    app = FastAPI()
    app.add_middleware(CompressionMiddleware)

    @app.get("/raw")
    def compressed() -> Response:
        return Response(content=raw, media_type="application/json")

    messages = asyncio.run(_collect(app, _http_scope()))
    body = _body(messages)

    assert body[:2] == b"\x1f\x8b"  # gzip magic
    assert gzip.decompress(body) == raw
    assert len(body) < len(raw)


def test_streamed_chunks_are_flushed_as_they_are_produced():
    """A compressed stream must not sit in zlib's buffer until the end.

    Without a per-chunk flush the first events of a long-lived stream arrive
    only once 8 KiB have accumulated -- or, for a chat notification stream,
    never.
    """
    app = FastAPI()
    app.add_middleware(CompressionMiddleware)

    @app.get("/stream")
    def stream() -> StreamingResponse:
        async def chunks():
            for index in range(3):
                yield f"event-{index}".encode()

        return StreamingResponse(chunks(), media_type="text/plain")

    messages = asyncio.run(_collect(app, _http_scope("/stream")))
    bodies = [
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    ]

    # Every chunk produced bytes on the wire before the stream ended.
    assert len(bodies) >= 3
    assert all(bodies[:-1]), bodies
    assert gzip.decompress(b"".join(bodies)) == b"event-0event-1event-2"


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("application/json", True),
        ("application/json; charset=utf-8", True),
        ("text/html; charset=utf-8", True),
        ("application/problem+json", True),
        ("image/svg+xml", True),
        ("application/wasm", True),
        ("text/event-stream", False),
        ("image/jpeg", False),
        ("video/mp4", False),
        ("application/zip", False),
        ("application/octet-stream", False),
        ("", False),
    ],
)
def test_is_compressible_type(content_type: str, expected: bool):
    assert is_compressible_type(content_type) is expected
