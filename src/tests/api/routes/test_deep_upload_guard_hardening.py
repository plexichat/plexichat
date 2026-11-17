"""Regression tests for upload-guard bypasses found during deep hardening.

Covers three fail-closed gaps in the shared media guard:

* a second JPEG container embedded after a valid leading image was invisible
  to the polyglot detector;
* event handlers with whitespace around ``=`` (``onerror =``) evaded the
  active-content scan;
* the 4 MiB scan head left a blind spot for the 5 MiB uploads the app accepts.
"""

from __future__ import annotations

import pytest

from src.core.avatars.manager import upload_guard

PNG = b"\x89PNG\r\n\x1a\n"
GIF = b"GIF89a"


# ---------------------------------------------------------------------------
# Polyglot: a second JPEG hidden after a valid leading container
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("leading", [PNG, GIF])
@pytest.mark.parametrize(
    "app_marker", [b"\xff\xd8\xff\xe0", b"\xff\xd8\xff\xe1", b"\xff\xd8\xff\xe8"]
)
def test_embedded_jpeg_second_container_is_a_polyglot(leading, app_marker):
    payload = leading + b"\x00" * 32 + app_marker + b"\x00" * 16

    containers = upload_guard.detect_containers(payload)
    expected_binary = "png" if leading == PNG else "gif"
    assert expected_binary in containers
    assert "jpeg" in containers, containers
    assert upload_guard.is_polyglot(payload) is True
    assert upload_guard.serve_payload_is_safe("image/png", payload) is False

    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.assert_safe_media_payload(payload)
    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.verify_upload_content("image/png", payload)


def test_honest_single_container_payloads_are_still_accepted():
    """The added JPEG signatures must not reject legitimate uploads."""
    for payload, mime in (
        (PNG + b"\x00" * 64, "image/png"),
        (GIF + b"\x00" * 64, "image/gif"),
        (b"\xff\xd8\xff\xe0" + b"\x00" * 64, "image/jpeg"),
    ):
        assert upload_guard.is_polyglot(payload) is False
        upload_guard.assert_safe_media_payload(payload)
        assert upload_guard.verify_upload_content(mime, payload) == mime
        assert upload_guard.serve_payload_is_safe(mime, payload) is True


# ---------------------------------------------------------------------------
# Active content: HTML/SVG allow whitespace around an attribute's ``=``
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "handler_attribute",
    [
        b'onerror ="alert(1)"',
        b"onload\t=alert(1)",
        b"onbegin\n=alert(1)",
        b"onclick   =  alert(1)",
        b"onpointerover =alert(1)",
    ],
)
def test_event_handler_with_whitespace_is_active_content(handler_attribute):
    payload = PNG + b"\x00" * 32 + b"<img src=x " + handler_attribute + b">"

    assert upload_guard.contains_active_content(payload) is True
    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.assert_safe_media_payload(payload)
    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.verify_upload_content("image/png", payload)
    assert upload_guard.serve_payload_is_safe("image/png", payload) is False


def test_whitespace_handler_scan_does_not_reject_honest_binary():
    """Binary data with a bare ``on`` word must not be flagged as active."""
    payload = PNG + b"\x00" * 32 + b"once upon a time on our on=ward journey"
    assert upload_guard.contains_active_content(payload) is False
    upload_guard.assert_safe_media_payload(payload)


# ---------------------------------------------------------------------------
# Scan coverage: 5 MiB uploads accepted by the app used to have a 1 MiB hole
# ---------------------------------------------------------------------------


def test_script_in_tail_of_five_mib_upload_is_rejected():
    """Active markup past the old 4 MiB cut-off is still detected."""
    script = b"<script>alert(1)</script>"
    payload = PNG + b"\x00" * (5 * 1024 * 1024 - len(PNG) - len(script)) + script
    assert len(payload) == 5 * 1024 * 1024

    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.assert_safe_media_payload(payload)
    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.verify_upload_content("image/png", payload)
    assert upload_guard.serve_payload_is_safe("image/png", payload) is False


def test_payload_larger_than_scan_limit_fails_closed():
    """Anything bigger than we can fully inspect is refused outright."""
    payload = PNG + b"\x00" * upload_guard._SCAN_LIMIT

    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.assert_safe_media_payload(payload)
    with pytest.raises(upload_guard.UploadContentError):
        upload_guard.verify_upload_content("image/png", payload)
    assert upload_guard.serve_payload_is_safe("image/png", payload) is False
