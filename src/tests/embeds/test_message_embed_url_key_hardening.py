"""Regression tests: proxy URL fields in message embeds must pass the strict
embed URL gate just like ``url``/``icon_url`` do.

Previously ``image.proxy_url`` / ``author.proxy_icon_url`` fell through to the
generic string path, so an internal SSRF/tracking URL survived into the stored
embed that clients render into image/attribute sinks.
"""

import pytest

from src.core.embeds.exceptions import InvalidUrlError
from src.core.embeds.validator import sanitize_message_embeds


@pytest.mark.parametrize(
    "payload",
    [
        [
            {
                "title": "x",
                "image": {
                    "url": "https://example.com/a.png",
                    "proxy_url": "http://169.254.169.254/latest/meta-data",
                },
            }
        ],
        [
            {
                "title": "x",
                "thumbnail": {
                    "url": "https://example.com/a.png",
                    "proxy_url": "http://127.0.0.1/internal",
                },
            }
        ],
        [
            {
                "title": "x",
                "video": {
                    "url": "https://example.com/v.mp4",
                    "proxy_url": "http://localhost/admin",
                },
            }
        ],
        [
            {
                "title": "x",
                "author": {"name": "a", "proxy_icon_url": "http://[::1]/secret"},
            }
        ],
    ],
)
def test_proxy_url_fields_block_ssrf(payload):
    with pytest.raises(InvalidUrlError):
        sanitize_message_embeds(payload)


@pytest.mark.parametrize(
    "proxy",
    [
        "javascript:alert(1)",
        'https://example.com/a" onload="alert(1)',
        "https://user:pass@example.com/a",
        "/api/v1/media/../../etc/passwd",
    ],
)
def test_proxy_url_fields_block_unsafe_values(proxy):
    with pytest.raises(Exception):
        sanitize_message_embeds(
            [
                {
                    "title": "x",
                    "image": {"url": "https://example.com/a.png", "proxy_url": proxy},
                }
            ]
        )


def test_safe_proxy_url_is_preserved():
    result = sanitize_message_embeds(
        [
            {
                "title": "x",
                "image": {
                    "url": "https://example.com/a.png",
                    "proxy_url": "https://cdn.example.com/a.png",
                },
            }
        ]
    )
    assert result[0]["image"]["proxy_url"] == "https://cdn.example.com/a.png"
