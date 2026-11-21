"""Regression: ``roles.manage`` is a SERVER-scoped permission, so the check
that lets a user mention an otherwise non-mentionable role must resolve at
server scope (no ``channel_id``).

Before the fix, ``validate_mentions`` passed ``channel_id`` into
``has_permission(..., "roles.manage", channel_id)``. The permission resolver
applies per-channel overrides whenever a channel id is supplied, so a member
granted ``roles.manage`` ONLY by a channel override -- who cannot manage roles
anywhere else (all real role CRUD checks resolve ``roles.manage`` at server
scope) -- could still mention a role the server owner deliberately marked
non-mentionable, defeating a server-wide configuration with a channel-local
grant. The mention fans the ping out to the role's members, so this is a real
privilege escalation, not a channel-local action.

This test proves the pre-fix escalation and locks the fix: a channel-only
grant must NOT authorize the mention, while a genuine server-wide grant still
must.
"""

import pytest

from src.core.notifications.manager.mention_validator import MentionValidationMixin
from src.core.notifications.models import Mention, MentionType

SERVER_ID = 100
CHANNEL_ID = 5
USER_ID = 1
PROTECTED_ROLE_ID = 50  # role["mentionable"] is False


class _DB:
    """Minimal DB stub returning the non-mentionable role row."""

    def __init__(self, roles):
        self._roles = roles

    def fetch_one(self, sql, params=()):
        if "FROM srv_roles" in sql:
            return self._roles.get(params[0])
        return None


class _Harness(MentionValidationMixin):
    """Just enough of the notification manager for ``validate_mentions``."""

    def __init__(self, db, servers):
        self._db = db
        self._servers = servers

    def _user_exists(self, user_id):
        return True


class _ChannelOnlyGrant:
    """servers stub: ``roles.manage`` granted ONLY by a channel override."""

    def __init__(self):
        self.calls = []

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.calls.append((permission, channel_id))
        return permission == "roles.manage" and channel_id == CHANNEL_ID


class _ServerWideGrant:
    """servers stub: ``roles.manage`` granted server-wide (no channel id)."""

    def __init__(self):
        self.calls = []

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.calls.append((permission, channel_id))
        return permission == "roles.manage" and channel_id is None


def _protected_role_mention():
    return [
        Mention(
            mention_type=MentionType.ROLE,
            target_id=PROTECTED_ROLE_ID,
            raw_text="<@&50>",
            start_pos=0,
            end_pos=6,
        )
    ]


def _validate(servers):
    db = _DB({PROTECTED_ROLE_ID: {"id": PROTECTED_ROLE_ID, "server_id": SERVER_ID, "mentionable": False}})
    return _Harness(db, servers).validate_mentions(
        USER_ID, _protected_role_mention(), server_id=SERVER_ID, channel_id=CHANNEL_ID
    )


@pytest.mark.notifications
def test_channel_only_roles_manage_cannot_bypass_non_mentionable_role():
    """A channel-scoped grant must not defeat a server-wide role setting.

    Pre-fix this mention came back valid (the channel override authorized the
    bypass); post-fix it is rejected and the permission is resolved at server
    scope.
    """
    servers = _ChannelOnlyGrant()
    result = _validate(servers)

    assert result[0].valid is False
    assert result[0].error == "Role is not mentionable"
    # The check must be issued at server scope, never with the channel id.
    assert ("roles.manage", None) in servers.calls
    assert ("roles.manage", CHANNEL_ID) not in servers.calls


@pytest.mark.notifications
def test_server_wide_roles_manage_still_bypasses_non_mentionable_role():
    """A genuine server-wide grant keeps the legitimate bypass behavior."""
    servers = _ServerWideGrant()
    result = _validate(servers)

    assert result[0].valid is True
    assert ("roles.manage", None) in servers.calls
