"""
Tests for the artifact route-layer helpers.

Focuses on the two behaviors that were fixed: personal/notes-scope
authorization (previously always 403'd) and PATCH-style payload merging on
``update_artifact`` (previously a whole-column replace).
"""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.routes.artifacts import (
    _authorize_scope,
    _deep_merge_payload,
    payload_revision,
    router,
    stale_payload_write,
)
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


# === rev guard: payload_revision / stale_payload_write ===


def test_payload_revision_extracts_integer():
    assert payload_revision({"content": "x", "rev": 7}) == 7


def test_payload_revision_float_truncates():
    assert payload_revision({"rev": 7.9}) == 7


def test_payload_revision_none_when_absent_or_non_numeric():
    assert payload_revision({}) is None
    assert payload_revision({"rev": "seven"}) is None
    assert payload_revision({"rev": None}) is None
    assert payload_revision(None) is None
    assert payload_revision("not a dict") is None


def test_payload_revision_rejects_bool():
    # bool is an int subclass; a True flag must not masquerade as rev 1.
    assert payload_revision({"rev": True}) is None
    assert payload_revision({"rev": False}) is None


def test_stale_write_requires_both_revs():
    # No rev on either side => never stale (other artifact types, legacy).
    assert stale_payload_write({}, {"content": "x"}) is False
    assert stale_payload_write({"content": "a", "rev": 5}, {"content": "b"}) is False
    assert stale_payload_write({}, {"content": "b", "rev": 5}) is False


def test_stale_write_rejects_older_and_equal_revs():
    assert stale_payload_write({"rev": 5}, {"rev": 4}) is True
    assert stale_payload_write({"rev": 5}, {"rev": 5}) is True


def test_stale_write_allows_newer_rev():
    assert stale_payload_write({"rev": 5}, {"rev": 6}) is False


def test_stale_write_first_write_always_allowed():
    # Persisted payload without a rev yet => the first stamped write wins.
    assert stale_payload_write({"content": "a"}, {"content": "b", "rev": 1}) is False


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
