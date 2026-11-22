"""Hot-path perf regressions for static-client asset serving.

Two bounded, local optimizations on the per-request static path:

* ``_send_file`` must stat a file once, not twice. The ETag used to re-stat the
  file purely to read ``st_mtime_ns`` even though the first stat already
  returned it -- one wasted syscall on *every* asset request (including 304s).
* ``_guess_content_type`` is a pure function of the path. Its result (including
  the fallback table) is memoized in a bounded LRU so repeated requests for the
  same hashed asset do not redo MIME detection.

Neither optimization touches authorization: static assets are public, and the
ETag still derives from the same file's mtime/size (now from a single, coherent
stat snapshot rather than two that could straddle a write).
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from types import SimpleNamespace


from src.static_client.router import (
    StaticClientMiddleware,
    _ResolvedFile,
)


def _minimal_middleware():
    """A middleware instance with only the fields ``_send_file`` reads."""
    mw = object.__new__(StaticClientMiddleware)
    mw._cfg = SimpleNamespace(
        cache_control=SimpleNamespace(html="no-cache"),
        security_headers=SimpleNamespace(
            x_content_type_options="nosniff",
            x_frame_options="DENY",
            referrer_policy="no-referrer",
            permissions_policy="none",
            content_security_policy="default-src 'self'",
        ),
    )
    return mw


def _collect_send():
    messages = []

    async def send(message):
        messages.append(message)

    return messages, send


def test_send_file_stats_once(tmp_path, monkeypatch):
    """A single asset request must perform exactly one ``stat()`` on the file."""
    target = tmp_path / "app-abc123.js"
    target.write_text("console.log(1);", encoding="utf-8")

    import pathlib

    real_stat = pathlib.Path.stat
    stats = []

    def counting_stat(self, *args, **kwargs):
        if Path(self) == target:
            stats.append(self)
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "stat", counting_stat)

    mw = _minimal_middleware()
    resolved = _ResolvedFile(
        file_path=target,
        cache_control="public, max-age=31536000, immutable",
        is_html=False,
    )
    messages, send = _collect_send()

    asyncio.run(mw._send_file(send, resolved, head_only=True, scope={"headers": []}))

    assert messages and messages[0]["status"] == 200
    assert len(stats) == 1, (
        f"_send_file stat'd the file {len(stats)} times; "
        "the ETag must reuse the first stat's st_mtime_ns"
    )


def test_guess_content_type_is_bounded_cached(monkeypatch):
    """Repeated content-type lookups for the same path must hit the LRU."""
    # Reset any warm entries so the test is deterministic regardless of order.
    StaticClientMiddleware._guess_content_type.cache_clear()

    real_guess = mimetypes.guess_type
    calls = []

    def counting_guess(url, *args, **kwargs):
        calls.append(url)
        return real_guess(url, *args, **kwargs)

    monkeypatch.setattr("src.static_client.router.mimetypes.guess_type", counting_guess)

    path = Path("/assets/app-abc123.js")
    first = StaticClientMiddleware._guess_content_type(path)
    second = StaticClientMiddleware._guess_content_type(path)

    assert first == second == "application/javascript"
    assert len(calls) == 1, (
        f"content-type detection ran {len(calls)} times for the same path; "
        "expected a bounded LRU hit on the second lookup"
    )
