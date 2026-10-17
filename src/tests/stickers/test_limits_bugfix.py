"""
Regression test for Bug S3: the HTTP-layer sticker upload ceiling was a hard
512 KiB, which rejected legitimate animated stickers (APNG/LOTTIE JSON) before
the manager's own size check could run. It is now 2 MiB.
"""

from src.api.routes.stickers import STICKER_MAX_UPLOAD_BYTES


def test_sticker_upload_limit_raised_to_2mib():
    assert STICKER_MAX_UPLOAD_BYTES == 2 * 1024 * 1024
    # Must be larger than the previous 512 KiB ceiling.
    assert STICKER_MAX_UPLOAD_BYTES > 524288
