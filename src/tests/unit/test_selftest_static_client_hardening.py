"""Regression tests for selftest static-client hardening.

Two weaknesses are covered:

* the internal-secret helper logged ``internal_secret[:8]`` into debug logs,
  leaking part of a shared secret into log aggregators (the same bug already
  fixed in ``docs.py``);
* asset paths scraped from the served ``index.html`` / vite manifest were fed
  into ``f"{base_url}{path}"`` unchecked, so tampered content could traverse
  out of ``/assets/`` or smuggle an authority and aim the privileged session
  (which carries ``X-Plexichat-Internal-Secret``) at an internal API route or
  an external host.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import src.core.selftest.services.static_client as static_mod
from src.core.selftest.services.static_client import (
    StaticClientTester,
    _safe_asset_path,
)

_SECRET = "f47ac10b58cc4372a5670e02b2c3d479"


@pytest.mark.unit
class TestInternalSecretNotLogged:
    def test_static_client_helper_never_logs_secret_value(self):
        """No slice of the secret may reach debug logs, only the fact it was set."""
        session = SimpleNamespace(headers={})

        with (
            patch("src.api.get_internal_secret", return_value=_SECRET),
            patch.object(static_mod.logger, "debug") as debug,
        ):
            StaticClientTester(SimpleNamespace())._add_internal_secret_header(session)

        assert session.headers["X-Plexichat-Internal-Secret"] == _SECRET
        messages = " ".join(str(c) for call in debug.call_args_list for c in call)
        assert _SECRET not in messages
        # The old bug logged ``internal_secret[:8]``; no prefix may appear.
        for n in range(1, len(_SECRET) + 1):
            assert _SECRET[:n] not in messages


@pytest.mark.unit
class TestSafeAssetPath:
    @pytest.mark.parametrize(
        "path",
        [
            "/assets/../../api/v1/admin/approvals.js",
            "/assets/..%2f..%2fadmin.css",
            "/assets/./x.js",
            "//evil.example/x.js",
            "/assets/x@evil.example.js",
            "/assets/x:evil.js",
            "/assets/x\\evil.js",
            "/assets/x.js?next=/admin",
            "/assets/x.js#fragment",
            "/assets/x\x00.js",
            "/assets/",
            "/api/v1/admin/approvals",
            None,
            42,
        ],
    )
    def test_rejects_traversal_authority_and_metadata(self, path):
        assert _safe_asset_path(path, require_assets_prefix=True) is None

    @pytest.mark.parametrize(
        "path",
        [
            "/assets/index-a1B2c3.js",
            "/assets/js/chunk-abc.css",
            "/assets/foo.bar_baz~1.js",
        ],
    )
    def test_accepts_confined_asset_paths(self, path):
        assert _safe_asset_path(path, require_assets_prefix=True) == path

    def test_builtin_spa_routes_still_allowed(self):
        # The final fetch loop also carries hard-coded SPA/static routes.
        assert _safe_asset_path("/app") == "/app"
        assert _safe_asset_path("/favicon.svg") == "/favicon.svg"


class _Response:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self.headers = {}
        self._payload = payload or {}

    def json(self):
        return self._payload


class _Session:
    """Records requested URLs and answers from a small responder table."""

    def __init__(self, responder):
        self.headers = {}
        self.calls = []
        self._responder = responder

    def get(self, url, **kwargs):
        self.calls.append(url)
        return self._responder(url)


@pytest.mark.unit
class TestDiscoveredAssetsNeverBecomeUnsafeUrls:
    def test_poisoned_index_and_manifest_paths_are_not_fetched(self):
        """A tampered index.html/manifest must not steer the privileged session."""
        base = "http://127.0.0.1:8756"
        hostile_html = (
            '<script src="/assets/index.js"></script>'
            '<script src="/assets/../../api/v1/admin/approvals.js"></script>'
            '<link href="/assets/..%2f..%2fadmin.css" rel="stylesheet">'
        )
        hostile_manifest = {
            "main": {"file": "assets/app.js", "css": ["assets/app.css"]},
            "escape": {"file": "/assets/../../api/v1/admin/x.js"},
            "external": {"file": "//evil.example/x.js"},
        }

        def responder(url):
            if url.endswith("/__nope_does_not_exist__.html"):
                return _Response(status_code=404)
            if url == base + "/":
                return _Response(text=hostile_html)
            if url == base + "/.vite/manifest.json":
                return _Response(payload=hostile_manifest)
            return _Response()

        session = _Session(responder)
        ctx = SimpleNamespace(base_url=base, make_session=lambda: session, results=[])

        with (
            patch(
                "src.core.selftest.services.static_client.config.get",
                return_value={"enabled": True, "serve": True},
            ),
            patch("src.api.get_internal_secret", return_value=_SECRET),
            patch.object(static_mod.logger, "warning") as warning,
        ):
            StaticClientTester(ctx).test_static_client()

        # No privileged request was ever sent to a traversal / authority URL.
        for url in session.calls:
            assert ".." not in url, url
            assert "evil.example" not in url, url
            assert "%2f" not in url.lower(), url

        # Legitimate discovered assets were still exercised.
        assert base + "/assets/index.js" in session.calls
        assert base + "/assets/app.js" in session.calls

        # The tampered references were surfaced, not silently ignored.
        rejected = [
            r
            for r in ctx.results
            if r.get("error") == "unsafe discovered asset path rejected"
        ]
        assert len(rejected) >= 2
        messages = " ".join(str(c) for call in warning.call_args_list for c in call)
        assert _SECRET not in messages
