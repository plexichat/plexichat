"""Regression: a server-scoped artifact must not be authorized by conversation
membership when the conversation-to-server binding cannot be resolved.

``_authorize_scope`` used to grant access when ``conv_server is None`` even
though ``server_id`` was a real server. That is a fail-open authorization
decision: a missing/invalid conversation metadata row, a soft-deleted
conversation or a transient DB read failure all resolve to ``None``, and each
one then let a mere conversation participant read or edit a server-scoped
artifact without ever holding the server permission. This pins the fail-closed
behaviour while proving legitimate conversation-bound and unscoped access is
unchanged.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

import src.api as api
from src.api.routes import artifacts_helpers as helpers
from src.core.servers.exceptions import PermissionDeniedError

pytestmark = [pytest.mark.security, pytest.mark.unit]

USER = 42
SERVER = 777
CONV = 123
PERMISSION = "artifact.edit"


def _patch_denied_server_permission():
    servers = MagicMock()
    servers.require_permission.side_effect = PermissionDeniedError("no")
    return patch.object(api, "get_servers", return_value=servers)


def _patch_member_conversation():
    messaging = MagicMock()
    messaging.is_participant.return_value = True
    return patch.object(api, "get_messaging", return_value=messaging)


def test_unresolvable_conversation_binding_denies_server_scope():
    """A real server_id with no resolvable conversation binding must be 403."""
    with (
        _patch_denied_server_permission(),
        _patch_member_conversation(),
        patch.object(helpers, "_conversation_server_id", return_value=None),
    ):
        with pytest.raises(HTTPException) as exc:
            helpers._authorize_scope(
                user_id=USER,
                conversation_id=CONV,
                server_id=SERVER,
                permission=PERMISSION,
            )
    assert exc.value.status_code == 403


def test_matching_conversation_binding_still_authorizes():
    """A conversation genuinely bound to the artifact's server stays allowed."""
    with (
        _patch_denied_server_permission(),
        _patch_member_conversation(),
        patch.object(helpers, "_conversation_server_id", return_value=SERVER),
    ):
        assert (
            helpers._authorize_scope(
                user_id=USER,
                conversation_id=CONV,
                server_id=SERVER,
                permission=PERMISSION,
            )
            is None
        )


def test_unscoped_conversation_access_still_authorizes():
    """A conversation-scoped artifact with no server stays allowed."""
    with (
        _patch_denied_server_permission(),
        _patch_member_conversation(),
        patch.object(helpers, "_conversation_server_id", return_value=None),
    ):
        assert (
            helpers._authorize_scope(
                user_id=USER,
                conversation_id=CONV,
                server_id=None,
                permission=PERMISSION,
            )
            is None
        )


def test_unrelated_conversation_binding_denied():
    """Membership in a conversation bound to a different server is denied."""
    with (
        _patch_denied_server_permission(),
        _patch_member_conversation(),
        patch.object(helpers, "_conversation_server_id", return_value=SERVER + 1),
    ):
        with pytest.raises(HTTPException) as exc:
            helpers._authorize_scope(
                user_id=USER,
                conversation_id=CONV,
                server_id=SERVER,
                permission=PERMISSION,
            )
    assert exc.value.status_code == 403
