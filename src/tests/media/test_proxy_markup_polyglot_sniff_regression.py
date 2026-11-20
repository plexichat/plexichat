"""Regression: the media proxy must scan the whole sniff window for SVG.

``ExternalProxy._sniff_content`` used to inspect only ``stripped[:9]``, so a
script-bearing SVG hidden behind any other prefix slipped past the gate and was
cached/served. The upload boundary already rejects the same polyglots via
``contains_active_markup``; the proxy must not be the weaker boundary. The
documented ``SNIFF_WINDOW_SIZE`` window is the one that must be inspected, not
just its first nine bytes.
"""

import pytest

from src.core.media.security.proxy import ExternalProxy, ProxyFetchError

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _sniff(body: bytes, content_type: str = "image/png"):
    """Drive ``_sniff_content`` without the network/storage dependencies."""
    proxy = object.__new__(ExternalProxy)
    return proxy._sniff_content(iter([body]), content_type, "http://example.test/x")


class TestProxyMarkupPolyglotSniff:
    def test_svg_at_start_is_still_rejected(self):
        with pytest.raises(ProxyFetchError, match="SVG"):
            _sniff(b"<svg onload=alert(1)>")

    def test_svg_behind_xml_comment_is_rejected(self):
        # A nine-byte prefix was enough to hide the marker from the old check.
        body = PNG_MAGIC + b"<!--x--><svg onload=alert(1)>" + b"\x00" * 32
        with pytest.raises(ProxyFetchError, match="SVG"):
            _sniff(body)

    def test_svg_with_whitespace_and_comment_prefix_is_rejected(self):
        body = b"\n\t<!-- padded -->\xff\xd8\xff<svg onload=alert(1)>"
        with pytest.raises(ProxyFetchError, match="SVG"):
            _sniff(body, "image/jpeg")

    def test_legitimate_raster_image_still_passes(self):
        peek, _rest = _sniff(PNG_MAGIC + b"\x00" * 64)
        assert peek.startswith(PNG_MAGIC)
