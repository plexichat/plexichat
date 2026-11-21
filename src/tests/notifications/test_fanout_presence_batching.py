"""Regression: mention fan-out hydrates recipient presence in ONE bulk read.

``create_notifications`` used to call ``self._presence.get_presence(user_id)``
once per candidate recipient. The only presence field that fan-out actually
consumes is ``current_channel_id`` (to suppress a ping when the recipient is
already looking at the channel). For a large role/everyone mention that meant
one cache lookup + a possible DB round trip + a Valkey ``HGETALL`` *per user*.

The fix resolves all recipients through ``get_presences(user_ids)`` once — that
helper already uses an ``MGET`` for cache hits and chunked ``IN (...)`` queries
for misses — and hands the per-user ``Presence`` to the decision helper.

Counters pinned below:

* before — N ``get_presence`` calls, zero ``get_presences`` calls;
* after  — zero per-user calls, exactly one bulk call for the whole audience.

Privacy / fail behaviour:

* a focused user suppresses only *their own* ping (no cross-user mixing);
* presence data is never surfaced outside the recipient's own decision;
* a failed bulk read degrades to "not focused" (deliver the ping), never
  crashes and never issues a per-user fallback that could mask the failure.
"""

import types

import pytest


SERVER = 600_100
CHANNEL = 700_100
OTHER_CHANNEL = 700_200


class _SpyPresence:
    """Bulk-only presence double with explicit per-user misuse detection."""

    def __init__(self, focused=None, fail=False):
        self.per_user_calls = []
        self.bulk_calls = []
        self.focused = dict(focused or {})
        self.fail = fail

    def _presence_for(self, uid):
        return types.SimpleNamespace(
            user_id=uid,
            current_channel_id=self.focused.get(uid),
        )

    def get_presence(self, user_id):
        self.per_user_calls.append(user_id)
        return self._presence_for(user_id)

    def get_presences(self, user_ids):
        self.bulk_calls.append(tuple(user_ids))
        if self.fail:
            raise RuntimeError("presence store unavailable")
        return [self._presence_for(uid) for uid in user_ids]


def _run(notification_manager, author, recipients, presence, message_id, conv_id):
    notification_manager._presence = presence
    # Any channel-scoped fan-out is permission-gated; keep every recipient.
    notification_manager._servers = types.SimpleNamespace(
        has_permission=lambda *args, **kwargs: True
    )
    content = " ".join(f"<@{u.id}>" for u in recipients)
    return notification_manager.create_notifications(
        message_id=message_id,
        author_id=author.id,
        conversation_id=conv_id,
        content=content,
        server_id=SERVER,
        channel_id=CHANNEL,
    )


@pytest.fixture
def fanout_users(auth_manager, messaging_manager):
    """Author + six recipients, plus a real message/conversation for FK safety."""
    author = auth_manager.register(
        username="fanout_author",
        email="fanout_author@example.com",
        password="TestPass123!",
    )
    recipients = [
        auth_manager.register(
            username=f"fanout_recipient_{i}",
            email=f"fanout_recipient_{i}@example.com",
            password="TestPass123!",
        )
        for i in range(6)
    ]
    conv = messaging_manager.create_group(
        owner_id=author.id,
        name="fanout",
        participant_ids=[u.id for u in recipients],
    )
    msg = messaging_manager.send_message(author.id, conv.id, "fanout")
    return author, recipients, msg.id, conv.id


@pytest.mark.notifications
def test_fanout_presence_uses_one_bulk_call(notification_manager, fanout_users):
    """Before: N per-user reads. After: one bulk read, identical recipients."""
    author, recipients, msg_id, conv_id = fanout_users
    expected = {u.id for u in recipients}
    spy = _SpyPresence()

    notified = _run(notification_manager, author, recipients, spy, msg_id, conv_id)

    # Result parity: every mentioned recipient is notified once.
    assert {n.user_id for n in notified} == expected
    assert len(notified) == len(recipients)

    # Query-count reduction: no per-user presence hydration survives, and the
    # whole audience is resolved through a single batched call.
    assert spy.per_user_calls == [], (
        f"fan-out still hydrates presence per user: {spy.per_user_calls}"
    )
    assert len(spy.bulk_calls) == 1, (
        f"expected exactly one bulk presence read, got {spy.bulk_calls}"
    )
    assert set(spy.bulk_calls[0]) == expected


@pytest.mark.notifications
def test_fanout_focus_suppression_is_per_recipient(notification_manager, fanout_users):
    """A focused user suppresses only their own ping, never another's."""
    author, recipients, msg_id, conv_id = fanout_users
    focused_user = recipients[0]
    other_focused = recipients[1]
    spy = _SpyPresence(
        focused={
            focused_user.id: CHANNEL,  # looking at this channel -> suppressed
            other_focused.id: OTHER_CHANNEL,  # elsewhere -> still notified
        }
    )

    notified = _run(notification_manager, author, recipients, spy, msg_id, conv_id)
    notified_ids = {n.user_id for n in notified}

    assert focused_user.id not in notified_ids
    assert other_focused.id in notified_ids
    assert notified_ids == {u.id for u in recipients} - {focused_user.id}
    # Privacy: presence was fetched in bulk only; no per-user read leaked in.
    assert spy.per_user_calls == []
    assert len(spy.bulk_calls) == 1


@pytest.mark.notifications
def test_fanout_presence_failure_fails_open(notification_manager, fanout_users):
    """A failed bulk read delivers every ping rather than crashing or leaking."""
    author, recipients, msg_id, conv_id = fanout_users
    spy = _SpyPresence(fail=True)

    notified = _run(notification_manager, author, recipients, spy, msg_id, conv_id)

    assert {n.user_id for n in notified} == {u.id for u in recipients}
    assert spy.per_user_calls == [], (
        "a failed bulk read must not fall back to per-user presence reads"
    )
    assert len(spy.bulk_calls) == 1
