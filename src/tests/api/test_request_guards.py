"""Unit tests for the request-boundary guards.

``read_upload_capped`` and ``read_body_capped`` are the helpers that put the
real size limit at the endpoint instead of trusting the reverse proxy. These
tests cover both halves of each guard: the declared size (``Content-Length`` /
``UploadFile.size``) and the bytes actually read, including the case where the
declared size lies.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest
from fastapi import HTTPException

from src.api.request_guards import (
    MAX_AVATAR_UPLOAD,
    MAX_EMOJI_UPLOAD,
    MAX_FEDERATION_BODY,
    MAX_SOUND_UPLOAD,
    MAX_VOICE_UPLOAD,
    read_body_capped,
    read_upload_capped,
)

LIMIT = 1024


class _FakeUpload:
    """Minimal ``UploadFile`` stand-in that records how much was requested."""

    def __init__(self, data: bytes, size: Optional[int] = None):
        self._data = data
        self.size = len(data) if size is None else size
        self.read_limit: Optional[int] = None

    async def read(self, limit: int = -1) -> bytes:
        self.read_limit = limit
        return self._data if limit < 0 else self._data[:limit]


class _FakeRequest:
    def __init__(self, body: bytes, headers: Optional[Dict[str, str]] = None):
        self._body = body
        self.headers = headers or {}

    async def body(self) -> bytes:
        return self._body


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _status(coro: Any) -> int:
    with pytest.raises(HTTPException) as exc:
        _run(coro)
    assert exc.value.status_code in (400, 413)
    return int(exc.value.status_code)


class TestUploadCap:
    def test_payload_exactly_at_limit_is_accepted(self) -> None:
        data = b"x" * LIMIT
        assert _run(read_upload_capped(_FakeUpload(data), LIMIT)) == data

    def test_declared_size_over_limit_is_rejected_without_reading(self) -> None:
        upload = _FakeUpload(b"x" * 10, size=LIMIT + 1)
        assert _status(read_upload_capped(upload, LIMIT)) == 413
        assert upload.read_limit is None, "declared oversize must not read the part"

    def test_body_over_limit_with_lying_declared_size_is_rejected(self) -> None:
        # ``size`` says it is small; the actual bytes exceed the cap.
        upload = _FakeUpload(b"x" * (LIMIT + 1), size=10)
        assert _status(read_upload_capped(upload, LIMIT)) == 413

    def test_read_is_capped_not_unbounded(self) -> None:
        upload = _FakeUpload(b"x" * 10)
        _run(read_upload_capped(upload, LIMIT))
        assert upload.read_limit == LIMIT + 1, (
            "an unbounded read() materialises the whole part, so the guard must "
            "request exactly one byte past the limit"
        )

    def test_absent_size_still_enforces_the_cap(self) -> None:
        upload = _FakeUpload(b"x" * (LIMIT + 1))
        upload.size = None
        assert _status(read_upload_capped(upload, LIMIT)) == 413


class TestBodyCap:
    def test_body_under_limit_is_returned(self) -> None:
        body = b'{"event": "ping"}'
        request = _FakeRequest(body, {"content-length": str(len(body))})
        assert _run(read_body_capped(request, LIMIT)) == body

    def test_body_exactly_at_limit_is_accepted(self) -> None:
        body = b"x" * LIMIT
        request = _FakeRequest(body, {"content-length": str(LIMIT)})
        assert _run(read_body_capped(request, LIMIT)) == body

    def test_declared_length_over_limit_is_rejected(self) -> None:
        request = _FakeRequest(b"", {"content-length": str(LIMIT + 1)})
        assert _status(read_body_capped(request, LIMIT)) == 413

    def test_non_numeric_content_length_is_a_400(self) -> None:
        request = _FakeRequest(b"{}", {"content-length": "not-a-number"})
        assert _status(read_body_capped(request, LIMIT)) == 400

    def test_negative_content_length_is_a_400(self) -> None:
        request = _FakeRequest(b"{}", {"content-length": "-5"})
        assert _status(read_body_capped(request, LIMIT)) == 400

    def test_body_over_limit_without_declared_length_is_rejected(self) -> None:
        request = _FakeRequest(b"x" * (LIMIT + 1))
        assert _status(read_body_capped(request, LIMIT)) == 413

    def test_body_over_limit_with_lying_declared_length_is_rejected(self) -> None:
        request = _FakeRequest(b"x" * (LIMIT + 1), {"content-length": "10"})
        assert _status(read_body_capped(request, LIMIT)) == 413

    def test_error_payload_shape_is_consistent(self) -> None:
        request = _FakeRequest(b"", {"content-length": str(LIMIT + 1)})
        with pytest.raises(HTTPException) as exc:
            _run(read_body_capped(request, LIMIT))
        detail = exc.value.detail
        assert detail["error"]["code"] == 413
        assert str(LIMIT) in detail["error"]["message"]


class TestLimits:
    def test_limits_are_positive_int_byte_counts(self) -> None:
        for limit in (
            MAX_AVATAR_UPLOAD,
            MAX_EMOJI_UPLOAD,
            MAX_SOUND_UPLOAD,
            MAX_VOICE_UPLOAD,
            MAX_FEDERATION_BODY,
        ):
            assert isinstance(limit, int) and not isinstance(limit, bool)
            assert limit > 0

    def test_media_limits_match_their_documented_sizes(self) -> None:
        assert MAX_AVATAR_UPLOAD == 10 * 1024 * 1024
        assert MAX_EMOJI_UPLOAD == 5 * 1024 * 1024
        assert MAX_SOUND_UPLOAD == 10 * 1024 * 1024
        assert MAX_VOICE_UPLOAD == 25 * 1024 * 1024

    def test_federation_envelopes_are_capped_much_lower(self) -> None:
        # Signed JSON envelopes stay small; the cap must not admit an upload.
        assert MAX_FEDERATION_BODY == 1024 * 1024
        assert MAX_FEDERATION_BODY < MAX_EMOJI_UPLOAD


class _RouteRequest(SimpleNamespace):
    """Request double used by the endpoint-level bound tests below."""


def test_guard_rejects_before_consuming_a_large_part() -> None:
    """A 25 MiB part declared to an avatar endpoint never reaches memory."""
    upload = _FakeUpload(b"", size=25 * 1024 * 1024)
    assert _status(read_upload_capped(upload, MAX_AVATAR_UPLOAD)) == 413
    assert upload.read_limit is None
