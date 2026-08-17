"""
Tests for the artifact route-layer helpers.

Focuses on the two behaviors that were fixed: personal/notes-scope
authorization (previously always 403'd) and PATCH-style payload merging on
``update_artifact`` (previously a whole-column replace).
"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.routes.artifacts import _authorize_scope, _deep_merge_payload, router
from src.api.schemas.artifacts import ArtifactCreateRequest, ArtifactUpdateRequest


def test_literal_artifact_routes_precede_dynamic_route():
    """Literal artifact subpaths must be declared before the base id route."""
    names = [route.name for route in router.routes]
    dynamic_index = names.index("get_artifact")
    assert names.index("_convert_upload_impl") < dynamic_index
    assert names.index("_export_artifact_impl") < dynamic_index
    assert names.index("_list_artifact_ops_impl") < dynamic_index


# === _authorize_scope: personal scope ===


def test_personal_scope_author_authorized():
    # server_id=None and conversation_id=None => personal scope; the author
    # must be allowed through (this is the bug that was fixed).
    assert (
        _authorize_scope(
            user_id=42,
            conversation_id=None,
            server_id=None,
            permission="artifact.edit",
            author_id=42,
        )
        is None
    )


def test_personal_scope_non_author_denied():
    with pytest.raises(HTTPException) as exc:
        _authorize_scope(
            user_id=42,
            conversation_id=None,
            server_id=None,
            permission="artifact.edit",
            author_id=7,
        )
    assert exc.value.status_code == 403


def test_personal_scope_missing_author_denied():
    # No author_id known => default to deny for personal scope.
    with pytest.raises(HTTPException) as exc:
        _authorize_scope(
            user_id=42, conversation_id=None, server_id=None, permission="artifact.edit"
        )
    assert exc.value.status_code == 403


def test_personal_scope_string_ids_normalized():
    assert (
        _authorize_scope(
            user_id=42,
            conversation_id=None,
            server_id=None,
            permission="artifact.edit",
            author_id="42",
        )
        is None
    )


def test_server_scope_ignores_author_mismatch_with_permission():
    # A server-scoped artifact is gated by server permission, never by author
    # identity. Without a permission module configured this must 403 rather
    # than silently pass on authorship.
    with pytest.raises(HTTPException) as exc:
        _authorize_scope(
            user_id=42,
            conversation_id=None,
            server_id=5,
            permission="artifact.edit",
            author_id=7,
        )
    assert exc.value.status_code == 403


# === _deep_merge_payload ===


def test_merge_nested_dicts():
    base = {"content": {"ops": [{"i": 1}]}, "rev": 1, "lang": "en"}
    patch = {"content": {"ops": [{"i": 2}]}}
    merged = _deep_merge_payload(base, patch)
    assert merged == {"content": {"ops": [{"i": 2}]}, "rev": 1, "lang": "en"}


def test_merge_keeps_unrelated_keys():
    base = {"content": "a", "rev": 5}
    merged = _deep_merge_payload(base, {"content": "b"})
    assert merged == {"content": "b", "rev": 5}


def test_merge_none_deletes_key():
    base = {"content": "a", "rev": 5}
    merged = _deep_merge_payload(base, {"content": None})
    assert merged == {"rev": 5}


def test_merge_none_base():
    assert _deep_merge_payload(None, {"a": 1}) == {"a": 1}


def test_merge_list_replaces_wholesale():
    base = {"strokes": [{"x": 0}]}
    merged = _deep_merge_payload(base, {"strokes": [{"x": 1}, {"x": 2}]})
    assert merged == {"strokes": [{"x": 1}, {"x": 2}]}


# === payload size bound (create + update) ===


def test_create_payload_over_1mb_is_rejected():
    oversized = {"content": "x" * (1024 * 1024 + 1)}
    with pytest.raises(ValidationError) as exc:
        ArtifactCreateRequest(artifact_type="upload", title="big", payload=oversized)
    assert "payload exceeds maximum size" in str(exc.value)


def test_update_payload_over_1mb_is_rejected():
    oversized = {"content": "x" * (1024 * 1024 + 1)}
    with pytest.raises(ValidationError) as exc:
        ArtifactUpdateRequest(payload=oversized)
    assert "payload exceeds maximum size" in str(exc.value)


def test_create_payload_under_limit_is_accepted():
    ok = {"content": "x" * 1024}
    body = ArtifactCreateRequest(artifact_type="upload", title="ok", payload=ok)
    assert body.payload["content"] == "x" * 1024
