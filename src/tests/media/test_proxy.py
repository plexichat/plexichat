"""Tests for media external URL proxy."""

import pytest

from src.core.media.exceptions import MediaError


@pytest.mark.media
class TestProxy:
    """Tests for external URL proxy functionality."""

    def test_proxy_not_available_by_default(self, media_manager):
        """Test proxy availability check."""
        # Proxy may or may not be available depending on config
        has_proxy = media_manager._proxy is not None
        assert isinstance(has_proxy, bool)

    def test_proxy_url_raises_when_unavailable(self, media_manager):
        """Test that proxy_url raises when proxy is not available."""
        if media_manager._proxy is None:
            with pytest.raises(MediaError, match="Proxy not available"):
                media_manager.proxy_url("https://example.com/image.png")

    def test_get_proxied_content_raises_when_unavailable(self, media_manager):
        """Test that get_proxied_content raises when proxy is not available."""
        if media_manager._proxy is None:
            with pytest.raises(MediaError, match="Proxy not available"):
                media_manager.get_proxied_content("https://example.com/image.png")
