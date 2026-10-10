"""Ratchet: push payload ids are decimal strings before they reach a device.

Web Push serialises the ``data`` dict with ``json.dumps`` verbatim (FCM and
APNs coerce values with ``str(...)`` first), so a numeric snowflake id in the
payload reaches a browser service worker as a JSON number and is rounded past
``2**53`` — the notification tap would then address the wrong message/channel.

``prepare_push_payload`` is the single point every platform builds from, so
this pins that the ids it emits are already strings and survive a JSON hop
exactly.
"""

from __future__ import annotations

import json

from src.core.notifications.models import MentionType, Notification

# 2**53 + 1: the first integer a JS ``Number`` cannot represent.
BIG = 9007199254740993


def _notification(**overrides) -> Notification:
    base = dict(
        id=BIG,
        user_id=BIG + 1,
        author_id=BIG + 2,
        message_id=BIG + 3,
        conversation_id=BIG + 4,
        server_id=BIG + 5,
        channel_id=BIG + 6,
        thread_id=BIG + 7,
        mention_type=MentionType.USER,
        content_preview="hi",
    )
    base.update(overrides)
    return Notification(**base)


def test_push_payload_ids_are_strings(notification_manager) -> None:
    payload = notification_manager.prepare_push_payload(_notification())

    for field, expected in (
        ("notification_id", BIG),
        ("message_id", BIG + 3),
        ("conversation_id", BIG + 4),
        ("server_id", BIG + 5),
        ("channel_id", BIG + 6),
        ("thread_id", BIG + 7),
    ):
        value = payload.data[field]
        assert value == str(expected), f"{field} not stringified: {value!r}"
        assert isinstance(value, str)

    # ``mention_type`` is a plain string value, not an id.
    assert payload.data["mention_type"] == "user"


def test_push_payload_round_trips_through_json_exactly(notification_manager) -> None:
    """Reproduce the Web Push serialization hop and assert no precision loss."""
    payload = notification_manager.prepare_push_payload(_notification())

    # Exactly what ``PushManager._send_web_push`` puts on the wire.
    wire = json.dumps(
        {"title": payload.title, "body": payload.body, "data": payload.data}
    )
    decoded = json.loads(wire)["data"]

    assert decoded["message_id"] == str(BIG + 3)
    assert decoded["channel_id"] == str(BIG + 6)


def test_push_payload_keeps_absent_ids_null(notification_manager) -> None:
    """A DM has no server/thread id; those must stay ``None``, not "None"."""
    payload = notification_manager.prepare_push_payload(
        _notification(server_id=None, thread_id=None)
    )

    assert payload.data["server_id"] is None
    assert payload.data["thread_id"] is None
