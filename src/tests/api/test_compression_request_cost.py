"""Regression: cut the fixed per-request header cost of CompressionMiddleware.

``CompressionMiddleware`` runs on every HTTP request. To decide gzip eligibility
it used to build ``Headers(scope=scope)`` -- which copies the entire header list
-- and then did two separate linear scans (``.get("accept-encoding")`` and
``"range" in ...``), all to read two headers. The single-pass helper
``_client_accepts_gzip_without_range`` replaces that without changing any
decision or any emitted byte.

These tests pin three things:

* the new helper's decision is byte-for-byte identical to the old ``Headers``
  logic across a matrix of header variations (duplicates, casing, range);
* the optimization only removes the list copy for the common list case while
  preserving the normalization side effect for non-list scopes;
* driving the real middleware produces messages identical to a reference
  middleware using the old logic, and the range/no-gzip safety gates still fire.
"""

import asyncio
from typing import Any, Dict, List, Tuple

import pytest
from starlette.datastructures import Headers

from src.api.middleware.compression import (
    CompressionMiddleware,
    _client_accepts_gzip_without_range,
    _GzipResponder,
)

Message = Dict[str, Any]
Header = Tuple[bytes, bytes]


def _old_decision(scope: dict) -> bool:
    """The original header-inspection logic, kept verbatim as a reference."""
    request_headers = Headers(scope=scope)
    accept_encoding = request_headers.get("accept-encoding", "")
    return not ("gzip" not in accept_encoding or "range" in request_headers)


def _scope(headers: List[Header], type_: str = "http") -> dict:
    return {"type": type_, "headers": list(headers)}


class _ReferenceCompression(CompressionMiddleware):
    """CompressionMiddleware with the OLD decision, for byte-for-byte diffing."""

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        if not _old_decision(scope):
            await self.app(scope, receive, send)
            return
        responder = _GzipResponder(self.app, self.minimum_size, self.compresslevel)
        await responder(scope, receive, send)


def _drive(middleware_cls, scope: dict) -> List[Message]:
    """Run a middleware over a deterministic downstream and return raw messages."""

    async def downstream(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send(
            {"type": "http.response.body", "body": b"x" * 2048, "more_body": False}
        )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    sent: List[Message] = []

    async def send(message):
        sent.append(message)

    middleware = middleware_cls(downstream)
    asyncio.run(middleware(scope, receive, send))
    return sent


class TestHeaderDecisionEquivalence:
    @pytest.mark.parametrize(
        "headers",
        [
            [],
            [(b"host", b"api.example.com")],
            [(b"accept-encoding", b"gzip")],
            [(b"accept-encoding", b"gzip, deflate, br")],
            [(b"accept-encoding", b"br")],
            [(b"accept-encoding", b"GZIP")],  # case-sensitive, like the old check
            [(b"accept-encoding", b"x-gzip-x")],
            [(b"accept-encoding", b"gzip"), (b"range", b"bytes=0-99")],
            [(b"range", b"bytes=0-99")],
            [(b"accept-encoding", b"gzip"), (b"range", b"bytes=0-99"), (b"host", b"x")],
            # duplicate accept-encoding: Headers.get returns the FIRST value
            [(b"accept-encoding", b"br"), (b"accept-encoding", b"gzip")],
            [(b"accept-encoding", b"gzip"), (b"accept-encoding", b"br")],
            # non-lowercase range key is NOT a match, exactly as before
            [(b"accept-encoding", b"gzip"), (b"Range", b"bytes=0-99")],
        ],
    )
    def test_decision_matches_old_headers_logic(self, headers):
        assert _client_accepts_gzip_without_range(_scope(headers)) is _old_decision(
            _scope(headers)
        )

    def test_accept_encoding_value_is_latin1_substring_equivalent(self):
        # A Latin-1 high byte followed by ASCII "gzip" must behave the same as
        # the decoded-string substring check the old code performed.
        headers = [(b"accept-encoding", b"\xffgzip")]
        assert _client_accepts_gzip_without_range(_scope(headers)) is _old_decision(
            _scope(headers)
        )


class TestNoHeaderListCopy:
    def test_list_scope_is_not_copied(self):
        headers = [(b"accept-encoding", b"gzip")]
        scope = {"type": "http", "headers": headers}
        assert _client_accepts_gzip_without_range(scope) is True
        # The optimization: the common list case keeps its identity.
        assert scope["headers"] is headers

    def test_non_list_scope_is_still_normalized(self):
        # ASGI allows any iterable; the old Headers(scope=...) normalized it to
        # a list as a side effect, which downstream code may rely on.
        headers = ((b"accept-encoding", b"gzip"),)
        scope = {"type": "http", "headers": headers}
        assert _client_accepts_gzip_without_range(scope) is True
        assert isinstance(scope["headers"], list)


class TestEndToEndByteIdentical:
    @pytest.mark.parametrize(
        "headers,type_",
        [
            ([(b"accept-encoding", b"gzip")], "http"),
            ([(b"accept-encoding", b"identity")], "http"),
            ([], "http"),
            ([(b"accept-encoding", b"gzip"), (b"range", b"bytes=0-10")], "http"),
            ([(b"accept-encoding", b"br"), (b"accept-encoding", b"gzip")], "http"),
            ([(b"accept-encoding", b"gzip")], "websocket"),
        ],
    )
    def test_emitted_messages_are_byte_identical(self, headers, type_):
        new = _drive(CompressionMiddleware, _scope(headers, type_))
        old = _drive(_ReferenceCompression, _scope(headers, type_))
        assert new == old

    def test_range_gate_still_prevents_reencoding(self):
        sent = _drive(
            CompressionMiddleware,
            _scope([(b"accept-encoding", b"gzip"), (b"range", b"bytes=0-10")]),
        )
        start = next(m for m in sent if m["type"] == "http.response.start")
        values = {k.lower(): v for k, v in start["headers"]}
        assert b"content-encoding" not in values
        assert _body(sent) == b"x" * 2048

    def test_no_gzip_gate_still_passes_through(self):
        sent = _drive(
            CompressionMiddleware, _scope([(b"accept-encoding", b"identity")])
        )
        start = next(m for m in sent if m["type"] == "http.response.start")
        values = {k.lower(): v for k, v in start["headers"]}
        assert b"content-encoding" not in values
        assert _body(sent) == b"x" * 2048


def _body(messages: List[Message]) -> bytes:
    return b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
