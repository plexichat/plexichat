"""SPA fallback HTML must stay in the fail-closed rate-limit tier.

The static middleware sits outside the global rate limiter and applies two
tiers: HTML documents fail closed when the limiter is unavailable, hashed
assets fail open. ``_resolve`` maps any path under a configured SPA prefix
(for example ``/app/x.js``) to the mapped HTML file, so classifying the request
by the *URL suffix* put those HTML responses into the permissive, fail-open
assets tier. An unauthenticated client could then keep fetching full SPA
documents even while the HTML limiter was refusing requests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from src.static_client.config import StaticClientConfig
from src.static_client.router import StaticClientMiddleware


def _middleware(root: Path) -> StaticClientMiddleware:
    cfg = StaticClientConfig(
        spa_routes={"/app": "app.html", "/settings": "settings.html"}
    )
    (root / "app.html").write_text("<html>app</html>", encoding="utf-8")
    (root / "settings.html").write_text("<html>settings</html>", encoding="utf-8")
    mgr = SimpleNamespace(
        config=cfg,
        current_install_path=lambda: root.resolve(),
        current_version=lambda: "1.0.0",
    )

    async def app(scope, receive, send):  # pragma: no cover - must not be reached
        raise AssertionError("downstream app must not be reached for static paths")

    return StaticClientMiddleware(app, mgr, cfg)


def _scope(path: str) -> dict:
    return {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
        "client": ("203.0.113.7", 50000),
        "scheme": "http",
    }


def _start(mw: StaticClientMiddleware, path: str) -> dict:
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(mw(_scope(path), None, send))
    return messages[0]


def test_spa_path_with_hashed_suffix_resolves_to_html(tmp_path: Path) -> None:
    """The suffix does not change what the SPA fallback actually serves."""
    mw = _middleware(tmp_path)
    resolved = mw._resolve(tmp_path.resolve(), "/app/x.js")
    assert resolved is not None
    assert resolved.is_html is True


def test_spa_fallback_paths_are_classified_as_html(tmp_path: Path) -> None:
    mw = _middleware(tmp_path)
    assert mw._is_html_request("/app") is True
    assert mw._is_html_request("/app/x.js") is True
    assert mw._is_html_request("/settings/a.css") is True
    # Genuine hashed assets stay in the cheap, cacheable assets tier.
    assert mw._is_html_request("/assets/app-abc123.js") is False


def test_spa_fallback_fails_closed_when_limiter_unavailable(
    tmp_path: Path, monkeypatch
) -> None:
    import src.core.ratelimit as ratelimit

    monkeypatch.setattr(ratelimit, "is_setup", lambda: False)
    mw = _middleware(tmp_path)

    # A plain SPA request is the documented fail-closed HTML response.
    assert _start(mw, "/app")["status"] == 429
    # The same HTML served under a hashed-looking suffix must not slip into the
    # fail-open assets tier.
    assert _start(mw, "/app/x.js")["status"] == 429
