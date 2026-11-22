"""Regression: static-client release fetches must re-validate redirect hops.

``requests`` follows 3xx responses by default and keeps custom headers such as
``PRIVATE-TOKEN`` across a cross-host redirect. The GitLab release client sends
that token on every API call, and the asset URLs it downloads come from an
external API response. A public endpoint answering
``302 Location: http://169.254.169.254/latest/meta-data/...`` would otherwise
turn one approved request into an authenticated request against an internal
address the SSRF gate never approved.

The fake transport below never touches the network. It emulates the one
property that matters: ``requests`` auto-follows a 302 (preserving custom
headers) unless the caller explicitly asks it not to.
"""

from __future__ import annotations

import pytest

import src.static_client.fetcher as fetcher
from src.static_client.config import GitLabConfig
from src.static_client.fetcher import FetchError, GitLabReleaseClient, _download_to_stream

API_BASE = "https://releases.example.com/api/v4"
PUBLIC_API = API_BASE + "/projects/1/releases"
PUBLIC_CDN = "https://cdn.example.com/dist.zip"
INTERNAL = "http://169.254.169.254/latest/meta-data/iam/security-credentials/role"


class _Resp:
    status_code = 200
    reason = "OK"

    def __init__(self, status_code=200, headers=None, json_data=None, chunks=()):
        self.status_code = status_code
        self.headers = headers or {}
        self._json = json_data
        self._chunks = tuple(chunks)

    def json(self):
        return self._json

    @property
    def text(self):
        return ""

    def iter_content(self, chunk_size):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _gate(monkeypatch, forbidden=()):
    """Stand-in for the shared SSRF gate; rejects only the internal target."""

    def check(url, field="url"):
        if any(url.startswith(prefix) for prefix in forbidden):
            raise ValueError(f"{field} resolved to a forbidden address")
        return None

    monkeypatch.setattr(fetcher, "parse_outbound_url", check, raising=False)


def _transport(monkeypatch, location, final_json=None, final_chunks=(b"payload",)):
    """Fake ``requests.get`` that follows 302s exactly like requests does."""
    calls = []

    def fake_get(url, **kwargs):
        headers = dict(kwargs.get("headers") or {})
        calls.append(
            {
                "url": url,
                "headers": headers,
                "allow_redirects": kwargs.get("allow_redirects", True),
            }
        )
        if url == location:
            return _Resp(200, json_data=final_json, chunks=final_chunks)
        # requests' default: follow the redirect and preserve custom headers.
        if kwargs.get("allow_redirects", True):
            return fake_get(
                location, **{**kwargs, "headers": headers, "allow_redirects": True}
            )
        return _Resp(302, {"Location": location})

    monkeypatch.setattr("requests.get", fake_get)
    return calls


def test_release_listing_never_fetches_internal_redirect(monkeypatch):
    monkeypatch.setenv("PLEXICHAT_GITLAB_TOKEN", "super-secret")
    _gate(monkeypatch, forbidden=(INTERNAL,))
    calls = _transport(monkeypatch, INTERNAL)

    client = GitLabReleaseClient(GitLabConfig(project_id=1, api_url=API_BASE))
    with pytest.raises(FetchError):
        client.list_releases()

    assert all(call["url"] != INTERNAL for call in calls)
    # The approved operator endpoint still gets its token; the refused
    # internal hop never sees a request at all.
    assert [c["url"] for c in calls] == [PUBLIC_API]
    assert calls[0]["headers"]["PRIVATE-TOKEN"] == "super-secret"


def test_public_redirect_is_followed_without_forwarding_credentials(monkeypatch):
    monkeypatch.setenv("PLEXICHAT_GITLAB_TOKEN", "super-secret")
    _gate(monkeypatch)
    calls = _transport(
        monkeypatch, PUBLIC_CDN, final_json=[{"tag_name": "1.0.0", "assets": {}}]
    )

    client = GitLabReleaseClient(GitLabConfig(project_id=1, api_url=API_BASE))
    releases = client.list_releases()

    assert [c["url"] for c in calls] == [PUBLIC_API, PUBLIC_CDN]
    assert releases and releases[0]["tag_name"] == "1.0.0"
    assert calls[0]["headers"]["PRIVATE-TOKEN"] == "super-secret"
    # Credentials must never be replayed to a different origin.
    assert "PRIVATE-TOKEN" not in calls[1]["headers"]


def test_asset_download_never_fetches_internal_redirect(monkeypatch, tmp_path):
    _gate(monkeypatch, forbidden=(INTERNAL,))
    calls = _transport(monkeypatch, INTERNAL)

    target = tmp_path / "dist.zip"
    with target.open("wb") as out:
        with pytest.raises(FetchError):
            _download_to_stream(PUBLIC_CDN, out, timeout=5)

    assert all(call["url"] != INTERNAL for call in calls)
