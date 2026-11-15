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
    """Read a raw request body, refusing anything larger than ``limit``."""
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
    body = await request.body()
    if len(body) > limit:
        raise _too_large(limit)
    return body
