"""Call-count regression for the per-invocation ``roles.manage`` memo.

``MentionValidationMixin.validate_mentions`` walks every mention in a message.
A non-mentionable role mention is only allowed through when the author holds
the **server-scoped** ``roles.manage`` permission. Before the fix that gate was
re-evaluated once per mention, so a message naming N non-mentionable roles
issued N identical ``has_permission(user, server, "roles.manage")`` calls even
though neither the viewer nor the scope changes across the fan-out.

The fix memoizes that single decision for the duration of one validation call,
keyed by the strict-int viewer id, the server scope and the current RBAC epoch.
These tests pin the measured reduction, the unchanged decisions, and the
safety properties (no cross-user reuse, no reuse across a revocation, strict
id coercion and fail-closed behaviour).
"""

from __future__ import annotations

import pytest

from src.core.notifications.manager.mention_validator import MentionValidationMixin
from src.core.notifications.models import Mention, MentionType

SERVER = 100
CHANNEL = 5
VIEWER = 7
N = 12
ROLE_BASE = 500


class _DB:
    """Minimal DB stub: role rows only, no mass-mention coverage data."""

    def __init__(self, roles):
        self._roles = roles

    def fetch_one(self, sql, params=()):
        if "FROM srv_roles" in sql:
            return self._roles.get(params[0])
        return None


class _Harness(MentionValidationMixin):
    def __init__(self, db, servers):
        self._db = db
        self._servers = servers

    def _user_exists(self, user_id):
        return True


class _CountingServers:
    def __init__(self, grant=False):
        self.grant = grant
        self.calls = []

    def has_permission(self, user_id, server_id, permission, channel_id=None):
        self.calls.append((user_id, server_id, permission, channel_id))
        return permission == "roles.manage" and bool(self.grant)


def _roles(n=N, server=SERVER):
    return {
        ROLE_BASE + i: {
            "id": ROLE_BASE + i,
            "server_id": server,
            "mentionable": False,
            "is_default": 0,
        }
        for i in range(n)
    }


def _mentions(n=N):
    return [
        Mention(
            mention_type=MentionType.ROLE,
            target_id=ROLE_BASE + i,
            raw_text=f"<@&{ROLE_BASE + i}>",
            start_pos=i * 10,
            end_pos=i * 10 + 8,
        )
        for i in range(n)
    ]


def _validate(servers, n=N, viewer=VIEWER, server=SERVER):
    return _Harness(_DB(_roles(n, server)), servers).validate_mentions(
        viewer, _mentions(n), server_id=server, channel_id=CHANNEL
    )


def _manage_checks(servers):
    return [c for c in servers.calls if c[2] == "roles.manage"]


@pytest.mark.notifications
def test_fanout_issues_one_roles_manage_check_not_one_per_mention():
    """N role mentions -> 1 authoritative call (was N before the fix)."""
    servers = _CountingServers(grant=False)
    result = _validate(servers)

    checks = _manage_checks(servers)
    assert len(checks) == 1, (
        f"expected one server-scoped roles.manage check for the whole "
        f"fan-out, got {len(checks)}: {checks}"
    )
    assert checks[0][1] == SERVER and checks[0][3] is None  # server scope

    # Same validated recipient/decision set as an otherwise identical
    # single-mention call (which cannot benefit from the fan-out memo).
    baseline = _validate(_CountingServers(grant=False), n=1)
    assert [(m.valid, m.error) for m in result] == [
        (m.valid, m.error) for m in baseline
    ] * N
    assert {m.target_id for m in result} == {ROLE_BASE + i for i in range(N)}
    assert all(m.error == "Role is not mentionable" for m in result)


@pytest.mark.notifications
def test_granted_verdict_is_reused_with_identical_recipient_set():
    servers = _CountingServers(grant=True)
    result = _validate(servers)

    assert len(_manage_checks(servers)) == 1
    assert len(result) == N
    assert all(m.valid is True and m.error is None for m in result)


@pytest.mark.notifications
def test_memo_does_not_cross_users():
    servers = _CountingServers(grant=False)
    _validate(servers, viewer=11)
    _validate(servers, viewer=12)

    checks = _manage_checks(servers)
    assert len(checks) == 2  # one per viewer, never one viewer reusing another
    assert {c[0] for c in checks} == {11, 12}


@pytest.mark.notifications
def test_memo_does_not_survive_a_role_revocation_epoch():
    from src.core.servers.channel_access_cache import get_channel_access_cache

    # Use a dedicated server id so bumping its epoch cannot disturb other
    # tests that share the process-wide version store.
    server = 91001
    cache = get_channel_access_cache()
    servers = _CountingServers(grant=False)

    _validate(servers, server=server)
    assert len(_manage_checks(servers)) == 1

    # A role/membership mutation bumps the RBAC epoch and flips the verdict.
    cache.bump(server, VIEWER)
    servers.grant = True
    result = _validate(servers, server=server)

    assert len(_manage_checks(servers)) == 2
    assert all(m.valid is True for m in result)


@pytest.mark.notifications
def test_bad_id_is_not_memoized_and_fails_closed():
    # A bool is not a valid snowflake: strict coercion must refuse to build a
    # memo key, so the authoritative gate still runs per mention and the
    # fail-closed (deny) outcome is unchanged.
    servers = _CountingServers(grant=False)
    result = _validate(servers, viewer=True)

    assert all(m.valid is False for m in result)
    assert len(_manage_checks(servers)) == N


@pytest.mark.notifications
def test_numeric_string_id_uses_the_same_memo_key_as_int():
    servers = _CountingServers(grant=False)
    _validate(servers, viewer="7")

    assert len(_manage_checks(servers)) == 1
