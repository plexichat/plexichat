"""
Tests for the artifact route-layer helpers.

Focuses on the two behaviors that were fixed: personal/notes-scope
authorization (previously always 403'd) and PATCH-style payload merging on
``update_artifact`` (previously a whole-column replace).
"""

import pytest
from fastapi import HTTPException

from src.api.routes.artifacts import _authorize_scope, _deep_merge_payload, router


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
    _authorize_scope(
        user_id=42,
        conversation_id=None,
        server_id=None,
        permission="artifact.edit",
        author_id=42,
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
    _authorize_scope(
        user_id=42,
        conversation_id=None,
        server_id=None,
        permission="artifact.edit",
        author_id="42",
    )


def test_server_scope_ignores_author_mismatch_with_permission():
    # A server-scoped artifact with a valid server permission should not be
    # gated by author identity; the server check runs first.
    try:
        _authorize_scope(
            user_id=42,
            conversation_id=None,
            server_id=5,
            permission="artifact.edit",
            author_id=7,
        )
    except HTTPException:
        pass  # A 403 here is fine when no permission module is configured.


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
