"""A streamed upload must be bounded by the bytes actually read, not the
client-declared size (the declared value is just a hint used to pick the
in-memory or temp-file path).
"""

import io

import pytest

from src.core.media.exceptions import FileSizeError

_PAYLOAD = b"hello world " * 256  # ~3 KiB of inert content
_CONTENT_TYPE = "application/octet-stream"


def test_understated_declared_size_is_rejected(media_manager):
    with pytest.raises(FileSizeError):
        media_manager.upload_stream(
            user_id=1,
            stream=io.BytesIO(_PAYLOAD),
            filename="a.txt",
            content_type=_CONTENT_TYPE,
            size=1,  # understated: the body is far larger
        )


def test_matching_declared_size_is_accepted(media_manager):
    result = media_manager.upload_stream(
        user_id=1,
        stream=io.BytesIO(_PAYLOAD),
        filename="a.txt",
        content_type=_CONTENT_TYPE,
        size=len(_PAYLOAD),
    )
    assert result is not None
    assert result.size == len(_PAYLOAD)
