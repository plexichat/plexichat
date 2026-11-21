"""Regression: reported artifact permissions must fail closed when the
conversation-to-server binding cannot be resolved.

``_authorize_scope`` was fixed to deny a server-scoped artifact when a mere
conversation member's ``conversation_id`` cannot be proven bound to that
server (commit ad389d44). ``_my_permissions`` -- the helper attached to every
``ArtifactResponse`` and documented as "authoritative server truth" -- still
used the old ``conv_server is None`` grant. A deleted/invalid conversation or a
transient DB read failure therefore reported ``can_view``/``can_edit`` = true
to a caller who had no server permission and could not actually access the
artifact. This pins the fail-closed behaviour while keeping legitimate and
unscoped grants intact.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import src.api as api
from src.api.routes import artifacts_helpers as helpers
from src.core.servers.exceptions import PermissionDeniedError

pytestmark = [pytest.mark.security, pytest.mark.unit]

USER = 42
OTHER = 99
SERVER = 777
CONV = 123


def _artifact() -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        conversation_id=CONV,
        server_id=SERVER,
        author_id=OTHER,
        artifact_type="whiteboard",
    )


def _patches(*, server_denied: bool = True, is_member: bool = True):
    servers = MagicMock()
    if server_denied:
        servers.require_permission.side_effect = PermissionDeniedError("no")
    else:
        servers.require_permission.return_value = True
    messaging = MagicMock()
    messaging.is_participant.return_value = is_member
    return (
        patch.object(api, "get_servers", return_value=servers),
        patch.object(api, "get_messaging", return_value=messaging),
    )


def test_unresolvable_binding_denies_reported_permissions():
    """A conversation member with no resolvable server binding gets no grant."""
    servers_patch, messaging_patch = _patches()
    with (
        servers_patch,
        messaging_patch,
        patch.object(helpers, "_conversation_server_id", return_value=None),
    ):
        perms = helpers._my_permissions(USER, _artifact())
    assert perms == {"can_edit": False, "can_view": False}


def test_matching_binding_still_grants_membership():
    """A conversation genuinely bound to the server keeps the membership grant."""
    servers_patch, messaging_patch = _patches()
    with (
        servers_patch,
        messaging_patch,
        patch.object(helpers, "_conversation_server_id", return_value=SERVER),
    ):
        perms = helpers._my_permissions(USER, _artifact())
    assert perms == {"can_edit": True, "can_view": True}


def test_server_permission_still_grants_independently_of_binding():
    """Holding the server permission grants access even if the binding is down."""
    servers_patch, messaging_patch = _patches(server_denied=False, is_member=False)
    with (
        servers_patch,
        messaging_patch,
        patch.object(helpers, "_conversation_server_id", return_value=None),
    ):
        perms = helpers._my_permissions(USER, _artifact())
    assert perms == {"can_edit": True, "can_view": True}


def test_unscoped_personal_artifact_still_grants_to_author_only():
    """A personal artifact is only granted to its author, as before."""
    personal = SimpleNamespace(
        id=2,
        conversation_id=None,
        server_id=None,
        author_id=USER,
        artifact_type="upload",
    )
    servers_patch, messaging_patch = _patches()
    with servers_patch, messaging_patch:
        assert helpers._my_permissions(USER, personal) == {
            "can_edit": True,
            "can_view": True,
        }
        assert helpers._my_permissions(OTHER, personal) == {
            "can_edit": False,
            "can_view": False,
        }
