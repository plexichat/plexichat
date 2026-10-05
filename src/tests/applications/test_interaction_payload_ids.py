"""Ratchet: the bot's INTERACTION_CREATE payload must carry string ids.

An interaction is delivered to a *bot* — an external consumer that parses the
JSON, very often in JavaScript. The handler stringified the top-level ids and
``data.id``/``data.target_id``, but the nested bodies it forwards verbatim
(``resolved`` users/members/channels/roles, ``options``) still carried raw
numbers, so a bot comparing them against its own ids missed the match.

The emitter now stringifies the whole payload before dispatching, so the ids
are exact regardless of the delivery path. These tests drive the real handler
with a stubbed events module and a fake db.
"""

from typing import Any, Dict, List

from src.core.applications.interactions.handler import InteractionHandler
from src.core.applications.models import (
    Interaction,
    InteractionData,
    InteractionType,
)

BIG = 9007199254740993  # past 2**53: a JSON number would lose precision


class _CapturingEvents:
    def __init__(self) -> None:
        self.events: List[Any] = []
        self.recipients: List[Any] = []

    def dispatch(self, event, user_ids=None, server_id=None, channel_id=None, **kwargs):
        self.events.append(event)
        self.recipients.append(user_ids)
        return 1


class _FakeDB:
    def __init__(self, row: Dict[str, Any]) -> None:
        self._row = row

    def fetch_one(self, query, params=None):
        return self._row


def _id_values(node, key=""):
    """Yield ``(key, value)`` for every id-typed leaf in a payload."""
    if isinstance(node, dict):
        for name, value in node.items():
            yield from _id_values(value, name)
    elif isinstance(node, list):
        for value in node:
            yield from _id_values(value, key)
    elif key == "id" or key.endswith("_id") or key.endswith("_ids"):
        yield key, node


def _handler():
    events = _CapturingEvents()
    handler = InteractionHandler(
        _FakeDB({"bot_id": BIG + 100}), {}, events_module=events
    )
    return handler, events


def _interaction() -> Interaction:
    data = InteractionData(
        id=BIG + 1,
        name="roll",
        # Discord-shaped resolved bodies: ids under ``id``/``*_id`` keys.
        resolved={
            "users": {
                "1": {"id": BIG + 2, "username": "ada"},
            },
            "members": {
                "1": {"user": {"id": BIG + 2}, "roles": [BIG + 3]},
            },
            "roles": {
                "1": {"id": BIG + 3, "name": "gm"},
            },
            "channels": {
                "1": {"id": BIG + 4, "type": 0},
            },
        },
        options=[{"name": "target", "type": 6, "value": BIG + 5, "id": BIG + 6}],
        target_id=BIG + 7,
    )
    return Interaction(
        id=BIG + 10,
        application_id=BIG + 11,
        interaction_type=InteractionType.APPLICATION_COMMAND,
        data=data,
        server_id=BIG + 12,
        channel_id=BIG + 13,
        user_id=BIG + 14,
        token="tok",
        version=1,
        message_id=BIG + 15,
        locale=None,
        server_locale=None,
        created_at=1,
    )


def test_dispatched_interaction_has_no_numeric_snowflake():
    handler, events = _handler()
    handler.dispatch_interaction(_interaction())

    assert len(events.events) == 1
    payload = events.events[0].data

    for key, value in _id_values(payload):
        assert isinstance(value, str), f"id {key!r} dispatched numeric: {value!r}"

    # Nested resolved bodies specifically (the ids that previously leaked).
    resolved = payload["data"]["resolved"]
    assert resolved["users"]["1"]["id"] == str(BIG + 2)
    assert resolved["members"]["1"]["user"]["id"] == str(BIG + 2)
    assert resolved["roles"]["1"]["id"] == str(BIG + 3)
    assert resolved["channels"]["1"]["id"] == str(BIG + 4)
    # Option ids are stringified too. ``value`` is an opaque, user-defined
    # slot (not an ``id``/``*_id`` field) and is intentionally left as-is.
    assert payload["data"]["options"][0]["id"] == str(BIG + 6)
    assert payload["data"]["options"][0]["value"] == BIG + 5


def test_interaction_recipient_is_the_bot():
    handler, events = _handler()
    handler.dispatch_interaction(_interaction())
    assert events.recipients[0] == [BIG + 100]
