"""Gateway builder dicts must carry snowflake ids as decimal strings.

``role_to_dict``, ``threads._thread_to_dict``, ``users._user_to_dict`` and
``get_admin_permissions_summary`` all claim to produce gateway-safe payloads.
A JSON number past 2**53 is rounded by JS clients, so the builders must
stringify before the central ``stringify_snowflake_ids`` backstop. This test
drives each builder with ids above 2**53 and fails on any numeric id.
"""

from types import SimpleNamespace

import pytest

from src.core.events.snowflakes import is_id_field

pytestmark = pytest.mark.unit

BIG = 2**53 + 1
BIG2 = 2**53 + 3


def _scan(node, path="$"):
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(value, bool):
                continue
            if is_id_field(key) and isinstance(value, int):
                found.append(f"{child} = {value} (numeric id)")
            if isinstance(value, int) and abs(value) >= 2**53:
                found.append(f"{child} = {value} (numeric snowflake)")
            found += _scan(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _scan(value, f"{path}[{index}]")
    return found


def test_role_to_dict_stringifies_ids():
    from src.api.routes.servers.roles import role_to_dict

    role = SimpleNamespace(
        id=BIG,
        name="Admin",
        color="#fff",
        position=1,
        permissions={"*": True},
        hoist=True,
        mentionable=True,
        is_default=False,
    )
    payload = role_to_dict(role, BIG)
    assert payload["id"] == str(BIG)
    assert payload["server_id"] == str(BIG)
    assert _scan(payload) == []


def test_thread_to_dict_stringifies_ids():
    from src.api.routes.threads import _thread_to_dict

    thread = SimpleNamespace(
        id=BIG,
        channel_id=BIG2,
        server_id=BIG,
        owner_id=BIG2,
        name="t",
        thread_type=SimpleNamespace(value="public"),
        state=SimpleNamespace(value="active"),
        parent_message_id=BIG,
        message_count=1,
        member_count=2,
        created_at="now",
        last_message_at=None,
        locked=False,
    )
    payload = _thread_to_dict(thread)
    assert payload["id"] == str(BIG)
    assert payload["parent_message_id"] == str(BIG)
    assert _scan(payload) == []

    thread.parent_message_id = None
    payload = _thread_to_dict(thread)
    assert payload["parent_message_id"] is None
    assert _scan(payload) == []


def test_user_to_dict_stringifies_id_and_roundtrips():
    from src.api.routes.users.helpers import (
        _user_to_dict,
        _user_to_public_response,
        _user_to_response,
    )

    user = SimpleNamespace(
        id=BIG,
        username="bob",
        email="b@x.com",
        avatar_url=None,
        created_at=1,
        email_verified=True,
        totp_enabled=False,
        account_type=SimpleNamespace(value="user"),
        permissions={},
        badges=[],
    )
    payload = _user_to_dict(user)
    assert payload["id"] == str(BIG)
    assert _scan(payload) == []
    # The cached dict feeds back into int()-tolerant response converters.
    assert str(_user_to_response(payload, include_private=True).id) == str(BIG)
    assert str(_user_to_public_response(payload).id) == str(BIG)


def test_admin_permissions_summary_stringifies_ids():
    import src.core.admin.permissions as perm

    roles = [
        perm.AdminRole(
            id=BIG,
            name="r",
            description="d",
            permissions={"*": True},
            created_at=1,
            created_by=BIG2,
        )
    ]
    original = perm._get_admin_roles
    perm._get_admin_roles = lambda db, aid: roles  # noqa: E731
    try:
        summary = perm.get_admin_permissions_summary(object(), BIG)
    finally:
        perm._get_admin_roles = original
    assert summary["admin_id"] == str(BIG)
    assert summary["roles"][0]["id"] == str(BIG)
    assert _scan(summary) == []


def test_thread_emit_accepts_string_ids_for_routing(monkeypatch):
    from src.core import events
    from src.core.events import gateway_emit
    from src.api.routes.threads import _thread_to_dict

    thread = SimpleNamespace(
        id=BIG,
        channel_id=BIG2,
        server_id=BIG,
        owner_id=BIG2,
        name="t",
        thread_type=SimpleNamespace(value="public"),
        state=SimpleNamespace(value="active"),
        parent_message_id=None,
        message_count=0,
        member_count=0,
        created_at="now",
        last_message_at=None,
        locked=False,
    )
    payload = _thread_to_dict(thread)

    calls = []
    monkeypatch.setattr(events, "is_setup", lambda: True)

    def fake_dispatch(event, **kwargs):
        calls.append((event, kwargs))
        return 1

    monkeypatch.setattr(events, "dispatch", fake_dispatch)
    gateway_emit.emit_thread_create(payload)
    gateway_emit.emit_thread_update(payload)

    assert len(calls) == 2
    for event, kwargs in calls:
        assert _scan(event.data) == []
        assert kwargs["channel_id"] == BIG2
        assert kwargs["server_id"] == BIG
