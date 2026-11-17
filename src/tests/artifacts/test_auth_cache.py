"""Per-request authorization memo tests.

Artifact listings call the same permission/membership/metadata helpers once
per item. These tests pin that a shared ``_AuthCache`` collapses repeated
lookups without changing any authorization decision.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

import src.api as api
from src.api.routes.artifacts_helpers import (
    _AuthCache,
    _authorize_scope,
    _conversation_server_id,
    _is_conversation_member,
    _require_server_permission,
    _validate_artifact_scope,
)


def test_conversation_server_id_is_looked_up_once_per_id():
    cache = _AuthCache()
    db = MagicMock()
    db.fetch_one.return_value = {"metadata": json.dumps({"server_id": 777})}
    with patch.object(api, "get_db", return_value=db):
        assert _conversation_server_id(42, cache) == 777
        assert _conversation_server_id(42, cache) == 777
    assert db.fetch_one.call_count == 1


def test_conversation_server_id_caches_misses_too():
    cache = _AuthCache()
    db = MagicMock()
    db.fetch_one.return_value = None
    with patch.object(api, "get_db", return_value=db):
        assert _conversation_server_id(99, cache) is None
        assert _conversation_server_id(99, cache) is None
    assert db.fetch_one.call_count == 1


def test_scope_validation_seeds_the_conversation_server_cache():
    """The metadata row read for scope validation is not queried twice."""
    cache = _AuthCache()
    db = MagicMock()
    db.fetch_one.side_effect = [
        {"metadata": json.dumps({"server_id": 777})},
        {"id": 777},
    ]
    with patch.object(api, "get_db", return_value=db):
        scope = _validate_artifact_scope(42, None, None, cache)
    assert scope == (42, None, 777)
    assert cache.conversation_servers[42] == 777
    # Authorization reuses the seeded value instead of re-reading the row.
    with patch.object(api, "get_db", return_value=db):
        assert _conversation_server_id(42, cache) == 777
    assert db.fetch_one.call_count == 2


def test_permission_and_membership_results_are_reused():
    cache = _AuthCache()
    servers = MagicMock()
    with patch.object(api, "get_servers", return_value=servers):
        assert _require_server_permission(1, 5, "artifact.view", cache) is True
        assert _require_server_permission(1, 5, "artifact.view", cache) is True
    assert servers.require_permission.call_count == 1

    messaging = MagicMock()
    messaging.is_participant.return_value = True
    assert _is_conversation_member(messaging, 42, 1, cache) is True
    assert _is_conversation_member(messaging, 42, 1, cache) is True
    assert messaging.is_participant.call_count == 1


def test_authorize_scope_decisions_are_unchanged_by_cache():
    """A cache hit must not silently widen or narrow access."""
    cache = _AuthCache()
    servers = MagicMock()
    with patch.object(api, "get_servers", return_value=servers):
        _authorize_scope(1, None, 5, "artifact.view", cache=cache)
        _authorize_scope(1, None, 5, "artifact.view", cache=cache)
    assert servers.require_permission.call_count == 1

    # Denied scope stays denied on a second (cached) attempt.
    from src.core.servers.exceptions import PermissionDeniedError

    servers.require_permission.side_effect = PermissionDeniedError("no")
    with patch.object(api, "get_servers", return_value=servers):
        denied = _AuthCache()
        try:
            _authorize_scope(2, None, 5, "artifact.view", cache=denied)
        except HTTPException as exc:
            assert exc.status_code == 403
        else:  # pragma: no cover - defensive
            raise AssertionError("denied scope must raise 403")
