"""Regression tests for artifact storage path and authorization security."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from src.core.artifacts.storage import (
    ArtifactAccessDenied,
    UnsafeStoragePath,
    authorize_artifact_access,
    safe_storage_key,
    safe_storage_path,
)


@pytest.fixture()
def storage_base(tmp_path):
    base = tmp_path / "exports"
    base.mkdir()
    return base


def test_valid_nested_path_is_confined(storage_base):
    target = safe_storage_path(storage_base, "tenant/2026/11/report.pdf")
    assert target == storage_base / "tenant/2026/11/report.pdf"
    assert os.path.commonpath([str(storage_base), str(target)]) == str(storage_base)


@pytest.mark.parametrize(
    "part",
    [
        "../outside.txt",
        "../../etc/passwd",
        "a/../../outside",
        "/etc/passwd",
        "..\\..\\secrets",
        "C:\\Windows\\win.ini",
        "nested/\x00/evil",
        "",
    ],
)
def test_traversal_and_absolute_parts_are_rejected(storage_base, part):
    with pytest.raises(UnsafeStoragePath):
        safe_storage_path(storage_base, part)


def test_symlink_escape_is_detected_even_when_target_exists(tmp_path, storage_base):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.txt"
    secret.write_text("top secret")
    (storage_base / "link").symlink_to(outside, target_is_directory=True)

    with pytest.raises(UnsafeStoragePath):
        safe_storage_path(storage_base, "link/secret.txt", must_exist=True)
    with pytest.raises(UnsafeStoragePath):
        safe_storage_path(storage_base, "link/new.txt")


def test_final_component_symlink_escape_is_detected(tmp_path, storage_base):
    outside = tmp_path / "outside.txt"
    outside.write_text("secret")
    (storage_base / "escape.txt").symlink_to(outside)

    with pytest.raises(UnsafeStoragePath):
        safe_storage_path(storage_base, "escape.txt", must_exist=True)


def test_storage_key_normalises_and_rejects_traversal():
    assert safe_storage_key("tenant/2026/./report.pdf") == "tenant/2026/report.pdf"
    assert safe_storage_key("tenant\\2026\\report.pdf") == "tenant/2026/report.pdf"
    for bad in ("../secret", "/absolute", "a/../../b", "C:\\x", "", "\x00"):
        with pytest.raises(UnsafeStoragePath):
            safe_storage_key(bad)


def _artifact(artifact_id=7):
    return SimpleNamespace(
        id=artifact_id,
        author_id=42,
        conversation_id=99,
        server_id=3,
    )


def test_idor_rejects_mismatched_artifact_id():
    with pytest.raises(ArtifactAccessDenied):
        authorize_artifact_access(
            requested_id=8,
            artifact=_artifact(7),
            user_id=42,
            can_access=lambda **kwargs: True,
        )


def test_idor_rejects_unauthorized_and_failed_authorizers():
    with pytest.raises(ArtifactAccessDenied):
        authorize_artifact_access(
            requested_id=7,
            artifact=_artifact(7),
            user_id=5,
            can_access=lambda **kwargs: False,
        )

    def broken(**kwargs):
        raise RuntimeError("membership service down")

    with pytest.raises(ArtifactAccessDenied):
        authorize_artifact_access(
            requested_id=7,
            artifact=_artifact(7),
            user_id=42,
            can_access=broken,
        )


def test_authorized_access_gets_the_right_scope():
    seen = {}

    def allow(**kwargs):
        seen.update(kwargs)
        return True

    authorize_artifact_access(
        requested_id="7",
        artifact=_artifact(7),
        user_id="42",
        can_access=allow,
    )
    assert seen["user_id"] == 42
    assert seen["conversation_id"] == 99
    assert seen["server_id"] == 3
    assert seen["author_id"] == 42
    assert seen["permission"] == "artifact.view"
