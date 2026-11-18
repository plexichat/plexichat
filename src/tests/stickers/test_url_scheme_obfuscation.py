"""Regression tests for sticker URL scheme obfuscation bypasses.

The raw-string scheme check used by ``add_sticker`` could be bypassed with
HTML character references that browsers decode when the URL is rendered into
an ``<img src>`` attribute. The sanitised value must be derived from the
browser-normalised form instead.
"""

import pytest

from src.core.stickers.exceptions import InvalidStickerNameError
from src.core.stickers.models import PackType, StickerFormat


@pytest.fixture
def personal_pack(sticker_manager):
    return sticker_manager.create_pack(1, "URL guard pack", pack_type=PackType.PERSONAL)


@pytest.mark.parametrize(
    "bad_url",
    [
        "javascript:alert(1)",
        "javascript&#58;alert(1)",
        "javascript&#x3a;alert(1)",
        "javascript&colon;alert(1)",
        "&#106;avascript:alert(1)",
        "java\nscript:alert(1)",
        "\x01javascript:alert(1)",
        "data:text/html,x",
        "data&#58;text/html,x",
        "file&#58:///etc/passwd",
    ],
)
def test_add_sticker_rejects_obfuscated_schemes(
    sticker_manager, personal_pack, bad_url
):
    with pytest.raises(InvalidStickerNameError):
        sticker_manager.add_sticker(
            1, personal_pack.id, "bad", StickerFormat.PNG, bad_url, 10
        )


@pytest.mark.parametrize(
    "good_url",
    [
        "https://cdn.example.com/s/x.png",
        "/media/stickers/x.png",
        "url",
    ],
)
def test_add_sticker_accepts_safe_urls(sticker_manager, personal_pack, good_url):
    sticker = sticker_manager.add_sticker(
        1, personal_pack.id, "good", StickerFormat.PNG, good_url, 10
    )
    assert sticker.url == good_url
