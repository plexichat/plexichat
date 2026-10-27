"""Gzip response compression.

Why this lives in the app instead of only in nginx: the client image
reverse-proxies ``/api/`` on the same origin, but a deployment is free to put
the API anywhere -- a bare ``uvicorn`` process, a Cloudflare Tunnel, a
platform that terminates TLS itself -- and those deployments would otherwise
send every JSON response uncompressed. Compressing in the app covers all of
them; nginx still compresses the static bundle, which this never sees.

Why not Starlette's ``GZipMiddleware``: it decides from ``Content-Length``
alone. That is fine for JSON, but this API also streams attachments (which can
be ranged, already-compressed video, or ``Content-Disposition: attachment``
exports) and serves user-uploaded media. Re-encoding a byte-range response
breaks the range, and gzipping an already-compressed JPEG or zip burns CPU for
roughly zero bytes saved. This middleware decides per response instead:
content type, disposition, existing encoding and status all have a say.

Streaming is incremental: each body chunk is flushed to the client with
``Z_SYNC_FLUSH`` so a compressed stream does not sit in zlib's buffer until the
next 8 KiB boundary. ``text/event-stream`` is never compressed (a gzip stream
buffers, which would stall server-sent events).
"""

import zlib
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Content types that compress well and are worth the CPU. ``text/*`` is
# handled separately, with ``text/event-stream`` carved back out below.
COMPRESSIBLE_TYPES = frozenset(
    {
        "application/json",
        "application/ld+json",
        "application/manifest+json",
        "application/problem+json",
        "application/x-ndjson",
        "application/javascript",
        "application/x-javascript",
        "application/xml",
        "application/xhtml+xml",
        "application/wasm",
        "image/svg+xml",
    }
)

# Streaming text types that must stay uncompressed: a gzip stream buffers, so
# compressing them delays or stalls delivery of individual events.
UNCOMPRESSED_TEXT_TYPES = frozenset({"text/event-stream"})

# Statuses whose body must be left exactly as produced: a partial response's
# byte offsets are the whole point of the range, and 204/304 have no body at
# all. Everything outside 2xx (errors, redirects) is left alone too -- those
# bodies are tiny and a proxy may be rewriting the response.
_SKIP_STATUSES = frozenset({204, 206, 304})


def is_compressible_type(content_type: str) -> bool:
    """Whether a media type is worth gzipping."""
    media_type = content_type.split(";", 1)[0].strip().lower()
    if not media_type:
        return False
    if media_type in UNCOMPRESSED_TEXT_TYPES:
        return False
    if media_type.startswith("text/"):
        return True
    return media_type in COMPRESSIBLE_TYPES


class CompressionMiddleware:
    """Gzip responses a client asked for and that are worth compressing.

    Skipped entirely when the client did not advertise ``gzip``, when the
    request carries a ``Range`` header (ranges are answered with byte offsets
    the client already computed against the identity representation), or for
    non-HTTP scopes (the WebSocket gateway).
    """

    def __init__(
        self,
        app: ASGIApp,
        minimum_size: int = 1024,
        compresslevel: int = 6,
    ) -> None:
        self.app = app
        self.minimum_size = minimum_size
        # 6 is the knee of the gzip curve: within a few percent of level 9 on
        # JSON for a fraction of the CPU. The static bundle is precompressed
        # at level 9 at build time, where the cost is paid once.
        self.compresslevel = compresslevel

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_headers = Headers(scope=scope)
        accept_encoding = request_headers.get("accept-encoding", "")
        if "gzip" not in accept_encoding or "range" in request_headers:
            await self.app(scope, receive, send)
            return

        responder = _GzipResponder(self.app, self.minimum_size, self.compresslevel)
        await responder(scope, receive, send)


class _GzipResponder:
    """Wrap ``send`` and gzip the body of compressible responses."""

    def __init__(self, app: ASGIApp, minimum_size: int, compresslevel: int) -> None:
        self.app = app
        self.minimum_size = minimum_size
        self.compresslevel = compresslevel
        self.send: Send = _unattached_send
        self.start_message: Message | None = None
        self.compress = False
        self.compressor: Any = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        self.send = send
        await self.app(scope, receive, self.send_with_compression)

    def _should_compress(self, message: Message) -> bool:
        status = message.get("status", 200)
        if status < 200 or status >= 300 or status in _SKIP_STATUSES:
            return False

        headers = Headers(raw=message["headers"])
        # Some upstream layer already encoded (or encrypted) the body; a second
        # encoding would be nested gzip the client cannot see through.
        if headers.get("content-encoding"):
            return False

        if not is_compressible_type(headers.get("content-type", "")):
            return False

        # A download is handed to the user as-is; re-encoding it also strips the
        # octet-exact length clients use for progress and resume.
        if "attachment" in headers.get("content-disposition", "").lower():
            return False

        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) < self.minimum_size:
                    return False
            except ValueError:
                # Unparseable length: fall through and compress. The body is
                # streamed, so there is no declared length to trust anyway.
                pass
        return True

    async def send_with_compression(self, message: Message) -> None:
        message_type = message["type"]

        if message_type == "http.response.start":
            self.start_message = message
            self.compress = self._should_compress(message)
            if self.compress:
                headers = MutableHeaders(raw=message["headers"])
                # The compressed length is unknown until the body is done, so
                # the declared length must go; the server falls back to chunked
                # transfer-encoding.
                if "content-length" in headers:
                    del headers["content-length"]
                headers["content-encoding"] = "gzip"
                headers.add_vary_header("Accept-Encoding")
            await self.send(message)
            return

        if message_type == "http.response.pathsend":
            # ``sendfile`` responses are produced by the server, not by us; the
            # start message already went out, so pass the pathsend through.
            await self.send(message)
            return

        if message_type != "http.response.body":
            await self.send(message)
            return

        if not self.compress:
            await self.send(message)
            return

        if self.compressor is None:
            # 16 + MAX_WBITS selects the gzip wrapper (header + CRC) instead of
            # a bare deflate stream.
            self.compressor = zlib.compressobj(
                self.compresslevel, zlib.DEFLATED, 16 + zlib.MAX_WBITS
            )

        body = message.get("body", b"") or b""
        more_body = message.get("more_body", False)
        # Z_SYNC_FLUSH per chunk keeps a streamed response flowing instead of
        # holding bytes back until zlib's internal buffer fills.
        chunk = self.compressor.compress(body)
        chunk += self.compressor.flush(
            zlib.Z_SYNC_FLUSH if more_body else zlib.Z_FINISH
        )
        message["body"] = chunk
        await self.send(message)


async def _unattached_send(message: Message) -> None:  # pragma: no cover
    raise RuntimeError("send awaitable not set")
