"""Path-traversal invariants for every storage path builder.

Storage paths are derived from user-controlled names (upload filenames, avatar
identifiers, artifact download names, archive member names). Every builder must
reduce its input to a single safe component and prove the result stays inside
its base directory. The invariants below are pure and deterministic: fixed
strings, temporary directories and mocks only.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from src.core.avatars.manager.upload_guard import (
    PathGuardError,
    check_archive_entries,
    confined_path,
    content_disposition,
    header_safe_filename,
    sanitize_upload_filename,
)
from src.core.security.hardening import HardeningViolation, safe_join

TRAVERSAL_NAMES = [
    "../../etc/passwd",
    "..\\..\\windows\\system32\\drivers\\etc\\hosts",
    "/etc/passwd",
    "C:\\evil\\name.mp3",
    "..",
    "../..",
    "a/../b",
    "....//....//etc/passwd",
    "evil\x00.png",
    "evil\r\nX-Injected: 1.png",
]


# ---------------------------------------------------------------------------
# Shared upload filename / path confinement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", TRAVERSAL_NAMES)
def test_sanitize_upload_filename_is_a_single_safe_component(name):
    result = sanitize_upload_filename(name)
    assert result
    assert "/" not in result
    assert "\\" not in result
    assert ".." not in result
    assert "\x00" not in result
    assert "\r" not in result and "\n" not in result


def test_sanitize_upload_filename_never_returns_empty():
    assert sanitize_upload_filename("") == "file"
    assert sanitize_upload_filename(None, default="attachment") == "attachment"
    assert sanitize_upload_filename("..") == "file"


PATH_TRAVERSAL_COMPONENTS = [
    "../../etc/passwd",
    "..\\..\\windows\\system32\\drivers\\etc\\hosts",
    "/etc/passwd",
    "C:\\evil\\name.mp3",
    "..",
    "../..",
    "a/../b",
    "....//....//etc/passwd",
    "evil\x00.png",
]


@pytest.mark.parametrize("name", PATH_TRAVERSAL_COMPONENTS)
def test_confined_path_rejects_traversal_components(tmp_path, name):
    with pytest.raises(PathGuardError):
        confined_path(tmp_path, name)


def test_confined_path_sanitizes_header_metacharacters(tmp_path):
    # Control characters are not path separators, so confined_path folds them
    # into a safe single component instead of raising; it still stays in base.
    target = confined_path(tmp_path, "evil\r\nX-Injected: 1.png")
    assert target.parent == tmp_path.resolve()
    assert "\r" not in target.name and "\n" not in target.name


def test_confined_path_keeps_honest_names_inside_base(tmp_path):
    target = confined_path(tmp_path, "avatar.png")
    assert target.parent == tmp_path.resolve()
    assert target.name == "avatar.png"


def test_confined_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path.parent / "outside-dir-for-path-invariant"
    try:
        outside.mkdir(exist_ok=True)
        link = tmp_path / "link"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks unavailable")
        with pytest.raises(PathGuardError):
            confined_path(tmp_path, "link", "payload.bin")
    finally:
        try:
            outside.rmdir()
        except OSError:
            pass


def test_safe_join_rejects_escaping_base(tmp_path):
    base = str(tmp_path)
    assert safe_join(base, "avatar.png") == str(tmp_path / "avatar.png")
    with pytest.raises(HardeningViolation):
        safe_join(base, "../secret")
    with pytest.raises(HardeningViolation):
        safe_join(base, "/etc/passwd")


def test_archive_member_names_cannot_traverse():
    for bad in TRAVERSAL_NAMES[:6]:
        with pytest.raises(PathGuardError):
            check_archive_entries([(bad, 16)])


def test_content_disposition_is_header_injection_proof():
    value = content_disposition('evil"\r\nX-Injected: 1.png')
    assert "\r" not in value and "\n" not in value
    assert value == 'attachment; filename="evil_X-Injected_1.png"'
    assert header_safe_filename("a/b.png") == "b.png"
    assert content_disposition("avatar.png", disposition="bogus").startswith(
        "attachment;"
    )


# ---------------------------------------------------------------------------
# Per-surface path builders
# ---------------------------------------------------------------------------


def test_media_storage_filename_is_confined():
    from src.core.media._validation import _ValidationMixin

    dummy = SimpleNamespace(_get_timestamp=lambda: 0)
    sanitize = _ValidationMixin._sanitize_filename
    for name in TRAVERSAL_NAMES:
        result = sanitize(dummy, name)
        assert result
        assert "/" not in result
        assert "\\" not in result
        assert ".." not in result
        assert "\x00" not in result


def test_avatar_identifier_and_filename_are_confined():
    from src.api.routes.avatars.errors import AvatarValidationError
    from src.api.routes.avatars.validation import parse_server_id, sanitize_filename

    assert sanitize_filename("../../etc/passwd") == "unnamed"
    assert sanitize_filename("..") == "unnamed"
    for sep in ("/", "\\", "\x00"):
        cleaned = sanitize_filename(f"evil{sep}name.png")
        assert sep not in cleaned

    for bad in ("../7", "-1", "abc", "1/2"):
        with pytest.raises(AvatarValidationError):
            parse_server_id(bad)
    assert parse_server_id("7") == 7


def test_artifact_download_name_and_format_are_confined():
    from fastapi import HTTPException

    from src.api.routes.artifacts.validation import (
        safe_download_filename,
        validate_export_format,
    )

    ascii_name, utf8_name = safe_download_filename("../../etc/passwd\r\nX: y", "txt")
    for value in (ascii_name, utf8_name):
        assert ".." not in value
        assert "/" not in value
        assert "\\" not in value
        assert "\r" not in value and "\n" not in value
    assert ascii_name.endswith(".txt")

    for bad in ("../../etc/passwd", "exe", "../html"):
        with pytest.raises(HTTPException):
            validate_export_format(bad)


def test_media_attachment_get_rejects_multi_segment_filename():
    """The serving route refuses any filename that is not a single segment."""
    import asyncio

    from fastapi import FastAPI

    import src.api.media_serving as media_serving

    app = FastAPI()
    media_serving.register_attachment_route(app)
    endpoint = next(
        r.endpoint
        for r in app.routes
        if getattr(r, "path", "").endswith("/media/attachments/{filename:path}")
    )
    with pytest.raises(Exception) as exc:
        asyncio.run(endpoint("nested/file.png", MagicMock()))
    assert getattr(exc.value, "status_code", None) == 400
