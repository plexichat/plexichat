"""Regressions for two avatar upload hardening gaps.

* a second JPEG whose first segment is not an APPn marker evaded the polyglot
  detector, so a PNG/GIF + JPEG polyglot was accepted as an honest image;
* EXIF orientation (via ``ImageOps.exif_transpose``) loaded the full decoded
  pixel buffer *before* the decompression-budget check ran, so a small crafted
  header could force a huge allocation before any limit was enforced.
"""

from __future__ import annotations

import io

import pytest


PNG_SIG = b"\x89PNG\r\n\x1a\n"
GIF_SIG = b"GIF89a"


def _make_png(width: int = 32, height: int = 32) -> bytes:
    from PIL import Image

    img = Image.new("RGB", (width, height), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Polyglot: a second JPEG beginning with any standard marker
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("leading", [PNG_SIG, GIF_SIG])
@pytest.mark.parametrize(
    "first_marker",
    [
        b"\xff\xdb",  # DQT
        b"\xff\xc0",  # SOF0
        b"\xff\xc4",  # DHT
        b"\xff\xda",  # SOS
        b"\xff\xfe",  # COM
        b"\xff\xee",  # APP14
    ],
)
def test_embedded_jpeg_with_non_appn_first_marker_is_polyglot(leading, first_marker):
    from src.core.avatars.manager import upload_guard

    payload = leading + b"\x00" * 24 + b"\xff\xd8" + first_marker + b"\x00" * 32
    expected = "png" if leading == PNG_SIG else "gif"

    containers = upload_guard.detect_containers(payload)
    assert expected in containers
    assert "jpeg" in containers, containers
    assert upload_guard.is_polyglot(payload) is True

    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.assert_safe_media_payload(payload)
    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.verify_upload_content("image/png", payload)
    assert upload_guard.serve_payload_is_safe("image/png", payload) is False


def test_real_jpeg_appended_to_png_is_detected():
    """A genuine JPEG (APP0 stripped so DQT is first) must still be caught."""
    from PIL import Image

    from src.core.avatars.manager import upload_guard

    buf = io.BytesIO()
    Image.new("RGB", (16, 16), (200, 100, 50)).save(buf, format="JPEG")
    jpeg = buf.getvalue()
    assert jpeg[:4] == b"\xff\xd8\xff\xe0"  # Pillow emits JFIF/APP0 first

    # A JPEG segment's 2-byte length includes the length bytes themselves.
    app0_len = int.from_bytes(jpeg[4:6], "big")
    jpeg = jpeg[:2] + jpeg[4 + app0_len :]
    assert jpeg[:4] == b"\xff\xd8\xff\xdb"  # now starts with DQT

    payload = PNG_SIG + jpeg
    assert "jpeg" in upload_guard.detect_containers(payload)
    assert upload_guard.is_polyglot(payload) is True
    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.assert_safe_media_payload(payload)


@pytest.mark.parametrize("first_marker", [b"\xff\xdb", b"\xff\xc0", b"\xff\xda"])
def test_single_jpeg_with_non_appn_marker_is_not_false_positive(first_marker):
    """A lone, honest JPEG using DQT/SOF/SOS must still be accepted."""
    from src.core.avatars.manager import upload_guard

    payload = b"\xff\xd8" + first_marker + b"\x00" * 32
    assert "jpeg" in upload_guard.detect_containers(payload)
    assert upload_guard.is_polyglot(payload) is False
    upload_guard.assert_safe_media_payload(payload)
    assert upload_guard.verify_upload_content("image/jpeg", payload) == "image/jpeg"
    assert upload_guard.serve_payload_is_safe("image/jpeg", payload) is True


# ---------------------------------------------------------------------------
# Decompression limits must be enforced before any pixel data is loaded
# ---------------------------------------------------------------------------


def test_decode_limit_checked_before_exif_transpose_loads_pixels(monkeypatch):
    from src.core.avatars.manager import processing, upload_guard
    from src.core.avatars.manager.manager import AvatarManager

    manager = AvatarManager(db=None)
    calls: list[str] = []

    def fail_limit(*args, **kwargs):
        calls.append("limit")
        raise upload_guard.DecompressionLimitError("budget exceeded")

    def fake_strip(img):
        calls.append("strip")
        return img

    monkeypatch.setattr(upload_guard, "check_image_decode_limits", fail_limit)
    monkeypatch.setattr(processing, "strip_image_metadata", fake_strip)

    with pytest.raises(ValueError, match="decompression limit exceeded"):
        manager._process_image(_make_png(), "image/png")

    # Before the fix, strip_image_metadata (which calls ImageOps.exif_transpose
    # -> image.load()) ran first and had already allocated the pixel buffer.
    assert calls == ["limit"], calls


def test_exif_rotation_still_applied_after_reorder():
    """Moving the limit check earlier must not change the final orientation."""
    from PIL import Image

    from src.core.avatars.manager.manager import AvatarManager

    manager = AvatarManager(db=None)
    exif = Image.Exif()
    exif[274] = 6  # ROTATE_270: 40x20 -> 20x40
    buf = io.BytesIO()
    Image.new("RGB", (40, 20), (10, 20, 30)).save(buf, format="PNG", exif=exif)

    processed, width, height, animated = manager._process_image(
        buf.getvalue(), "image/png"
    )
    assert (width, height) == (20, 40)
    assert animated is False
    assert Image.open(io.BytesIO(processed)).size == (20, 40)
