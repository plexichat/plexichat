"""
Regression tests for embeds audit fixes (E1-E4).

- E1: preview images are rewritten to the authenticated media-proxy route so
  clients never load the raw external image (which would bypass the proxy's
  SSRF / content-type / magic-byte checks).
- E2: ``_check_embed_links_permission`` must fail closed (deny) when the
  servers module is unavailable, not fail open (allow).
- E3: ``create_embed(..., message_id=...)`` rolls back the embed row when
  attaching to the message fails, leaving no orphaned embed.
- E4: ``parse_url_metadata`` reports ``valid: False`` when scraping fails.
"""

import pytest

from src.core.embeds.link_preview import LinkPreviewService, PreviewMetadata
from src.core.embeds.exceptions import PermissionDeniedError


# --------------------------------------------------------------------------- #
# E1 — preview image is proxied
# --------------------------------------------------------------------------- #


class _FakeProxied:
    id = 1
    content_type = "image/png"


class _FakeMediaProxy:
    def fetch(self, url, force_refresh=False):
        return _FakeProxied()


def test_preview_image_url_is_rewritten_to_proxy(db, monkeypatch):
    service = LinkPreviewService(db, media_proxy=_FakeMediaProxy())
    monkeypatch.setattr(
        service,
        "_fetch_with_redirects",
        lambda *a, **k: (b"<html></html>", "https://example.com/page", "text/html"),
    )
    meta = PreviewMetadata(
        url="https://example.com/page",
        title="T",
        image_url="https://img.example.com/x.png",
        site_name="S",
        embed_type="link",
    )
    monkeypatch.setattr(service, "_parse_metadata", lambda html, base: meta)

    result = service.generate_preview(1, "https://example.com/page")
    assert result.image_url.startswith("/api/v1/media/proxy?url=")


# --------------------------------------------------------------------------- #
# E2 — embed_links permission fails closed without servers
# --------------------------------------------------------------------------- #


def test_embed_links_permission_fails_closed_without_servers(db_and_modules):
    db, auth, messaging, servers, embeds = db_and_modules
    embeds._manager._servers = None
    assert embeds._manager._check_embed_links_permission(1, 1, None) is False


# --------------------------------------------------------------------------- #
# E3 — orphaned embed rolled back on attach failure
# --------------------------------------------------------------------------- #


def test_orphaned_embed_rolled_back_on_attach_failure(users_with_dm, monkeypatch):
    user1, user2, dm, msg, embeds = users_with_dm
    mgr = embeds._manager

    def _fail(*a, **k):
        raise PermissionDeniedError("attach blocked")

    monkeypatch.setattr(mgr, "attach_embed_to_message", _fail)

    before = mgr._db.fetch_one("SELECT COUNT(*) AS c FROM embed_embeds")["c"]
    with pytest.raises(PermissionDeniedError):
        embeds.create_embed(user1.id, title="t", description="d", message_id=msg.id)
    after = mgr._db.fetch_one("SELECT COUNT(*) AS c FROM embed_embeds")["c"]
    assert after == before


# --------------------------------------------------------------------------- #
# E4 — parse_url_metadata reports invalid on failure
# --------------------------------------------------------------------------- #


def test_parse_url_metadata_invalid_on_failure(db_and_modules, monkeypatch):
    db, auth, messaging, servers, embeds = db_and_modules

    def _boom(*a, **k):
        raise RuntimeError("scrape failed")

    monkeypatch.setattr(embeds._manager, "_scrape_url_metadata", _boom)

    result = embeds.parse_url_metadata("https://example.com/x", user_id=1)
    assert result["valid"] is False
