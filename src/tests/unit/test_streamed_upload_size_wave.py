"""A streamed upload must be bounded by the bytes actually read, not the
client-declared size (the declared value is just a hint used to pick the
in-memory or temp-file path).
"""

import io

import pytest

from src.core.media.exceptions import FileSizeError

# A valid PNG magic header followed by inert padding.
_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_understated_declared_size_is_rejected(media_manager):
    payload = _PNG + b"\x00" * 2048
    with pytest.raises(FileSizeError):
        media_manager.upload_stream(
            user_id=1,
            stream=io.BytesIO(payload),
            filename="a.png",
            content_type="image/png",
            size=1,  # understated: the body is far larger
        )


def test_matching_declared_size_is_accepted(media_manager):
    payload = _PNG + b"\x00" * 2048
    result = media_manager.upload_stream(
        user_id=1,
        stream=io.BytesIO(payload),
        filename="a.png",
        content_type="image/png",
        size=len(payload),
    )
    assert result is not None
    assert result.size == len(payload)
