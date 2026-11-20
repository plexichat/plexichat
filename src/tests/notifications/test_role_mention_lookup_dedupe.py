"""Regression tests for role-mention validation query amplification.

Before the fix, ``validate_mentions`` called ``role_exists()`` and then
``get_role()`` for each valid role mention -- two identical
``SELECT ... FROM srv_roles WHERE id = ?`` round trips per mention, and both
repeated when the same role was mentioned more than once in a single message.
A role mention is resolved once per distinct role id now, with per-call
memoisation and unchanged fail-closed checks.
"""

import pytest

from src.core.notifications.manager.mention_validator import MentionValidationMixin
from src.core.notifications.models import Mention, MentionType


class _CountingDB:
    """Minimal DB stub that counts ``srv_roles`` lookups."""

    def __init__(self, roles):
        self._roles = roles
        self.role_selects = 0

    def fetch_one(self, sql, params=()):
        if "FROM srv_roles" in sql:
            self.role_selects += 1
            return self._roles.get(params[0])
        return None


class _Harness(MentionValidationMixin):
    """Just enough of the manager for ``validate_mentions``."""

    def __init__(self, db, servers=None):
        self._db = db
        self._servers = servers

    def _user_exists(self, user_id):
        return True


def _role_mentions(*role_ids):
    return [
        Mention(
            mention_type=MentionType.ROLE,
            target_id=rid,
            raw_text=f"<@&{rid}>",
            start_pos=0,
            end_pos=len(f"<@&{rid}>"),
        )
        for rid in role_ids
    ]


@pytest.mark.notifications
def test_one_role_lookup_per_distinct_role():
    """Two distinct valid role mentions cost two queries, not four."""
    roles = {
        11: {"id": 11, "server_id": 100, "mentionable": True},
        22: {"id": 22, "server_id": 100, "mentionable": True},
    }
    db = _CountingDB(roles)
    result = _Harness(db).validate_mentions(
        1, _role_mentions(11, 22), server_id=100, channel_id=5
    )

    assert all(m.valid for m in result)
    assert db.role_selects == 2


@pytest.mark.notifications
def test_duplicate_role_mentions_reuse_one_lookup():
    """The same role mentioned three times still costs a single query."""
    roles = {33: {"id": 33, "server_id": 100, "mentionable": True}}
    db = _CountingDB(roles)
    result = _Harness(db).validate_mentions(
        1, _role_mentions(33, 33, 33), server_id=100, channel_id=5
    )

    assert len(result) == 3
    assert all(m.valid for m in result)
    assert db.role_selects == 1


@pytest.mark.notifications
def test_missing_role_fails_closed():
    """A role that does not exist is still rejected, after one lookup."""
    db = _CountingDB({})
    result = _Harness(db).validate_mentions(
        1, _role_mentions(404), server_id=100, channel_id=5
    )

    assert result[0].valid is False
    assert result[0].error == "Role not found"
    assert db.role_selects == 1


@pytest.mark.notifications
def test_cross_server_and_non_mentionable_checks_preserved():
    """The single fetched row still drives both authorization checks."""
    roles = {
        1: {"id": 1, "server_id": 999, "mentionable": True},  # wrong server
        2: {"id": 2, "server_id": 100, "mentionable": False},  # not mentionable
        3: {"id": 3, "server_id": 100, "mentionable": True},  # fine
    }
    db = _CountingDB(roles)
    result = _Harness(db).validate_mentions(
        1, _role_mentions(1, 2, 3), server_id=100, channel_id=5
    )

    assert result[0].error == "Role not in this server"
    assert result[1].error == "Role is not mentionable"
    assert result[2].valid is True
    assert db.role_selects == 3
