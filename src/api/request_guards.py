"""Request-boundary guards for uploads and raw bodies.

``UploadFile.read()`` with no argument materialises the whole part into memory,
and ``request.body()`` does the same for an arbitrary payload, so an endpoint's
real limit was whatever the reverse proxy happened to allow. These helpers put
the limit at the endpoint: the declared size is checked first, then the read is
capped so a lying ``Content-Length`` cannot force the allocation either.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, UploadFile

MAX_AVATAR_UPLOAD = 10 * 1024 * 1024
MAX_EMOJI_UPLOAD = 5 * 1024 * 1024
MAX_SOUND_UPLOAD = 10 * 1024 * 1024
MAX_VOICE_UPLOAD = 25 * 1024 * 1024

#: Federated peers send signed JSON envelopes; nothing legitimate is near 1 MiB.
MAX_FEDERATION_BODY = 1024 * 1024


def _too_large(limit: int) -> HTTPException:
    return HTTPException(
        status_code=413,
        detail={"error": {"code": 413, "message": f"Payload exceeds {limit} bytes"}},
    )


async def read_upload_capped(file: UploadFile, limit: int) -> bytes:
    """Read an upload, refusing anything larger than ``limit`` bytes."""
    declared = getattr(file, "size", None)
    if declared is not None and declared > limit:
        raise _too_large(limit)
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise _too_large(limit)
    return data


async def read_body_capped(request: Any, limit: int) -> bytes:
    """Read a raw request body, refusing anything larger than ``limit``.

    ``request.body()`` takes no size argument: it buffers every chunk the ASGI
    server delivers before returning, so checking ``len(body)`` afterwards is
    too late to protect memory. A request with no trustworthy
    ``Content-Length`` (chunked transfer encoding, or a lying header) could
    therefore make an endpoint allocate far beyond its own, much smaller cap.

    Consume ``request.stream()`` incrementally instead and fail the instant the
    running byte count would cross ``limit``. The result is cached in
    ``request._body`` exactly as ``Request.body()`` does, so a dependency that
    caps the body and a route that later calls ``request.json()`` see the same
    bytes without re-consuming the stream.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            length = int(declared)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid Content-Length"}},
            ) from exc
        if length < 0:
            raise HTTPException(
                status_code=400,
                detail={"error": {"code": 400, "message": "Invalid Content-Length"}},
            )
        if length > limit:
            raise _too_large(limit)

    # A body already buffered by ``body()`` or middleware is in ``_body``; it
    # cannot be un-read, but the endpoint cap still applies to those bytes.
    cached = getattr(request, "_body", None)
    if cached is not None:
        if len(cached) > limit:
            raise _too_large(limit)
        return cached

    stream = getattr(request, "stream", None)
    if stream is None:
        # Minimal request doubles expose only ``body()``. Keep the post-read
        # cap as a fallback; real Starlette requests take the bounded path.
        body = await request.body()
        if len(body) > limit:
            raise _too_large(limit)
        return body

    chunks: list[bytes] = []
    total = 0
    async for chunk in stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > limit:
            raise _too_large(limit)
        chunks.append(chunk)
    body = b"".join(chunks)
    try:
        object.__setattr__(request, "_body", body)
    except (AttributeError, TypeError):  # pragma: no cover - exotic doubles
        pass
    return body
