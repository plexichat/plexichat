"""Regression: executable extensions hidden behind a trailing dot, an NTFS
alternate-data-stream tail or a second extension must not pass upload
validation.

The bytes/stream upload sinks used an ``os.path.splitext`` final-suffix check.
That let a PE binary named ``loader.exe.`` through: the stream sink's own
``_sanitize_filename`` preserves the trailing dot, while the serve path's
header sanitizer strips it, so the victim downloads a file named
``loader.exe``. The chunked-upload completion path reaches that same sink with
a client-chosen session filename, so it was affected too.
"""

import io

import pytest

from src.core.media.exceptions import FileTypeError
from src.core.security.hardening import sanitize_header_filename

PE_BYTES = b"MZ\x90\x00" + b"\x00" * 64

# Every one of these resolves to a blocked executable/script type on a
# Windows client or in the Content-Disposition header, but the final suffix
# seen by os.path.splitext() is harmless.
HIDDEN_EXECUTABLE_NAMES = (
    "loader.exe.",
    "loader.exe ",
    "loader.exe::$DATA",
    "loader.exe.png",
    "payload.js.",
)


def test_serve_header_normalises_trailing_dot_to_a_real_executable_name():
    # Documents the impact: the name that reaches the client is a .exe even
    # though the upload gate used to see only the final "." suffix.
    assert sanitize_header_filename("loader.exe.") == "loader.exe"


@pytest.mark.parametrize("filename", HIDDEN_EXECUTABLE_NAMES)
def test_streamed_upload_rejects_hidden_executable_suffix(
    media_manager, test_user, filename
):
    with pytest.raises(FileTypeError):
        media_manager.upload_stream(
            user_id=test_user.id,
            stream=io.BytesIO(PE_BYTES),
            filename=filename,
            content_type="application/octet-stream",
            size=len(PE_BYTES),
        )


@pytest.mark.parametrize("filename", ("loader.exe.", "loader.exe.png"))
def test_bytes_upload_rejects_hidden_executable_suffix(
    media_manager, test_user, filename
):
    with pytest.raises(FileTypeError):
        media_manager.upload_file(
            user_id=test_user.id,
            file_data=PE_BYTES,
            filename=filename,
            content_type="application/octet-stream",
        )


def test_benign_upload_still_succeeds(media_manager, test_user):
    # Non-vacuous control: the gate rejects the hidden executable suffix, not
    # octet-stream uploads in general.
    payload = b"just a text payload"
    result = media_manager.upload_stream(
        user_id=test_user.id,
        stream=io.BytesIO(payload),
        filename="notes.dat",
        content_type="application/octet-stream",
        size=len(payload),
    )
    assert result.filename == "notes.dat"
    assert result.size == len(payload)
