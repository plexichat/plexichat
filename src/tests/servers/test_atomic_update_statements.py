"""Each update builder must emit exactly one UPDATE statement.

The seven servers builders (server, channel, member, role, scheduled
event, template, onboarding step) used to issue one ``UPDATE`` per changed
column: N round trips and, worse, a non-atomic write where a mid-update
failure left a half-updated row. They now build a single atomic ``UPDATE``
assigning every changed column plus ``updated_at``.

These tests capture the SQL each builder really executes and assert a
single UPDATE was issued carrying every changed column (plus exactly one
``updated_at``). The no-duplicate-assignment shape is guarded separately
by ``test_update_builder_sql_guard.py``.
"""

from __future__ import annotations

import time
import uuid

from src.tests._conftest.sql_guard import (
    assigned_columns,
    capture_executed_sql,
    update_statements,
)


def _unique(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


def _assert_single_update(executor, action, label, expected_columns):
    """Run ``action``; assert exactly one UPDATE assigning ``expected``."""
    statements = update_statements(capture_executed_sql(executor, action))
    assert len(statements) == 1, (
        f"{label} must emit exactly one UPDATE statement "
        f"(atomic write), got {len(statements)}: {statements}"
    )
    assigned = assigned_columns(statements[0])
    for column in expected_columns:
        assert column in assigned, f"{label} dropped column {column!r}: {statements[0]}"
    assert assigned.count("updated_at") == 1, (
        f"{label} must write updated_at exactly once: {statements[0]}"
    )
    return statements[0]


def test_update_server_emits_single_update(server_with_members):
    server, owner, *_rest, servers = server_with_members

    _assert_single_update(
        servers._db,
        lambda: servers.update_server(
            user_id=owner.id,
            server_id=server.id,
            name="Renamed",
            max_reactions_per_message=25,
        ),
        "update_server",
        {"name", "max_reactions_per_message", "updated_at"},
    )


def test_update_channel_emits_single_update(server_with_members):
    server, owner, *_rest, servers = server_with_members
    channel = servers.create_channel(owner.id, server.id, _unique("chan"))

    _assert_single_update(
        servers._db,
        lambda: servers.update_channel(
            owner.id, channel.id, name=_unique("chan"), topic="topic"
        ),
        "update_channel",
        {"name", "topic_encrypted", "updated_at"},
    )


def test_update_member_emits_single_update(server_with_members):
    server, owner, _, member_user, *_rest, servers = server_with_members

    _assert_single_update(
        servers._db,
        lambda: servers.update_member(
            owner.id, server.id, member_user.id, nickname="nick", muted=True
        ),
        "update_member",
        {"nickname", "muted", "updated_at"},
    )


def test_update_role_emits_single_update(server_with_members):
    _, owner, _, _, _, admin_role, servers = server_with_members

    _assert_single_update(
        servers._db,
        lambda: servers.update_role(
            user_id=owner.id,
            role_id=admin_role.id,
            name="Renamed Role",
            color="#00FF00",
        ),
        "update_role",
        {"name", "color", "updated_at"},
    )


def test_update_scheduled_event_emits_single_update(server_with_members):
    server, owner, *_rest, servers = server_with_members
    event = servers.create_scheduled_event(
        user_id=owner.id,
        server_id=server.id,
        name="Standup",
        start_time=int(time.time() * 1000) + 3_600_000,
        event_type=servers.ScheduledEventType.EXTERNAL,
        location="Online",
    )

    _assert_single_update(
        servers._db,
        lambda: servers.update_scheduled_event(
            user_id=owner.id, event_id=event.id, name="Standup 2", location="Office"
        ),
        "update_scheduled_event",
        {"name", "location", "updated_at"},
    )


def test_update_template_emits_single_update(server_with_members):
    server, owner, *_rest, servers = server_with_members
    template = servers.create_template(owner.id, server.id, _unique("tpl"))

    _assert_single_update(
        servers._db,
        lambda: servers.update_template(
            owner.id, template.code, name=_unique("tpl"), is_public=True
        ),
        "update_template",
        {"name", "is_public", "updated_at"},
    )


def test_update_onboarding_step_emits_single_update(server_with_members):
    server, owner, *_rest, servers = server_with_members
    step = servers.create_onboarding_step(
        owner.id,
        server.id,
        servers.OnboardingStepType.READ_RULES,
        _unique("Rules"),
    )

    _assert_single_update(
        servers._db,
        lambda: servers.update_onboarding_step(
            owner.id, step.id, title=_unique("Rules"), position=2
        ),
        "update_onboarding_step",
        {"title", "position", "updated_at"},
    )
