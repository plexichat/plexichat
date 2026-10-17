"""Every gateway ``emit_*`` helper must serialize snowflake ids as strings.

The emitters in :mod:`src.core.events.gateway_emit` build the payload dicts
that reach connected clients. A 64-bit snowflake served as a JSON number is
rounded by the client past 2**53, so the event addresses the wrong entity. The
helpers stringify most fields by hand, and some run whole sub-objects through
``stringify_snowflake_ids``; this test drives each helper with ids above 2**53
and scans the dispatched payload for any numeric id or large integer, so a new
or edited emitter cannot regress the rule.
"""

import pytest

from src.core.events import gateway_emit
from src.core.events.snowflakes import is_id_field

pytestmark = pytest.mark.unit

# Distinct values above 2**53 so a rounded copy is easy to spot.
BIG = 2**53 + 1
BIG2 = 2**53 + 3
BIG3 = 2**53 + 5


def _scan(node, path="$"):
    """Return ``path = value`` for numeric ids or any integer >= 2**53."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(value, bool):
                continue
            if key == "revision":
                # A monotonically increasing revision is a real integer, not
                # an id, and is allowed to exceed 2**53.
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


# (helper name, callable) -- each builds one event with ids above 2**53.
CALLS = {
    "emit_user_update": lambda: gateway_emit.emit_user_update(
        {"id": BIG, "username": "u", "avatar_id": BIG2}
    ),
    "emit_message_delete_bulk": lambda: gateway_emit.emit_message_delete_bulk(
        BIG, [BIG2, BIG3], guild_id=BIG, actor_id=BIG2
    ),
    "emit_message_reaction_remove_all": lambda: (
        gateway_emit.emit_message_reaction_remove_all(
            BIG, BIG2, guild_id=BIG3, actor_id=BIG
        )
    ),
    "emit_poll_update": lambda: gateway_emit.emit_poll_update(
        BIG, BIG2, "vote", actor_id=BIG3, channel_id=BIG
    ),
    "emit_message_reaction_remove_emoji": lambda: (
        gateway_emit.emit_message_reaction_remove_emoji(
            BIG, BIG2, "👍", guild_id=BIG3, actor_id=BIG
        )
    ),
    "emit_channel_pins_update": lambda: gateway_emit.emit_channel_pins_update(
        BIG, guild_id=BIG2, pinned_message_ids=[BIG3]
    ),
    "emit_guild_update": lambda: gateway_emit.emit_guild_update(
        BIG, name="srv", owner_id=BIG2
    ),
    "emit_guild_role_create": lambda: gateway_emit.emit_guild_role_create(
        BIG, {"id": BIG2, "server_id": BIG, "color": 0}
    ),
    "emit_guild_role_update": lambda: gateway_emit.emit_guild_role_update(
        BIG, {"id": BIG2, "server_id": BIG, "color": 0}
    ),
    "emit_guild_role_delete": lambda: gateway_emit.emit_guild_role_delete(BIG, BIG2),
    "emit_guild_ban_add": lambda: gateway_emit.emit_guild_ban_add(
        BIG, BIG2, reason="r"
    ),
    "emit_guild_ban_remove": lambda: gateway_emit.emit_guild_ban_remove(BIG, BIG2),
    "emit_invite_create": lambda: gateway_emit.emit_invite_create(
        BIG, {"code": "abc", "server_id": BIG, "inviter_id": BIG2}
    ),
    "emit_invite_delete": lambda: gateway_emit.emit_invite_delete(BIG, "abc"),
    "emit_webhooks_update": lambda: gateway_emit.emit_webhooks_update(
        BIG, channel_id=BIG2
    ),
    "emit_thread_create": lambda: gateway_emit.emit_thread_create(
        {"id": BIG, "guild_id": BIG2, "parent_id": BIG3, "owner_id": BIG}
    ),
    "emit_thread_update": lambda: gateway_emit.emit_thread_update(
        {"id": BIG, "guild_id": BIG2, "parent_id": BIG3, "owner_id": BIG}
    ),
    "emit_thread_delete": lambda: gateway_emit.emit_thread_delete(
        BIG, guild_id=BIG2, parent_id=BIG3
    ),
    "emit_workbook_update": lambda: gateway_emit.emit_workbook_update(
        BIG,
        1,
        BIG2,
        {"comment_id": BIG3, "mention_user_ids": [BIG]},
    ),
    "emit_security_alert": lambda: gateway_emit.emit_security_alert(
        BIG, "session_revoked", {"session_id": BIG2}
    ),
}


def test_scanner_flags_numeric_ids():
    assert _scan({"id": BIG}) != []
    assert _scan({"id": str(BIG)}) == []
    assert _scan({"user_id": 42}) != []
    assert _scan({"count": 42}) == []
    assert _scan({"revision": 9007199254740993}) == []  # revision is numeric


@pytest.fixture
def captured(monkeypatch):
    from src.core import events

    collected = []
    monkeypatch.setattr(events, "is_setup", lambda: True)
    monkeypatch.setattr(events, "dispatch", lambda event, **kw: collected.append(event))
    return collected


def test_every_emit_helper_stays_inside_the_id_rule(captured):
    offenders = []
    for name, call in CALLS.items():
        before = len(captured)
        call()
        assert len(captured) == before + 1, f"{name} did not emit an event"
        hits = _scan(captured[-1].data)
        offenders += [f"{name}: {hit}" for hit in hits]

    assert not offenders, (
        "gateway emit helpers dispatched a numeric snowflake id; stringify it "
        "before it reaches the wire:\n" + "\n".join(offenders)
    )
