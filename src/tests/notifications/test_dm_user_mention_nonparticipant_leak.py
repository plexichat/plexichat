"""Regression: a USER mention in a DM must not notify a user who is not a
participant of that conversation.

A DM's message content is private to its participants. Before the fix, the
USER-mention fan-out in ``NotificationCreatorMixin.create_notifications`` only
checked that the target exists, is not the author and is not blocked. It never
checked conversation membership, and the channel-view recipient filter is a
no-op for DMs (no ``channel_id``), so an attacker could mention an unrelated
user in a DM and have that user receive a notification -- and, on push
dispatch, a push payload -- carrying the DM's private message content preview.
"""

import uuid

PRIVATE_BODY = "private-dm-body-marker"


def _mkuser(modules, name):
    suffix = uuid.uuid4().hex[:8]
    return modules.auth.register(
        username=f"{name}_{suffix}",
        email=f"{name}_{suffix}@example.com",
        password="TestPass123!",
    )


def test_dm_user_mention_does_not_notify_nonparticipant(modules):
    attacker = _mkuser(modules, "dmatt")
    dm_peer = _mkuser(modules, "dmpeer")
    victim = _mkuser(modules, "dmvictim")

    # victim is deliberately NOT a participant of this DM.
    dm = modules.messaging.create_dm(attacker.id, dm_peer.id)

    content = f"hey <@{victim.id}> look at this: {PRIVATE_BODY}"
    msg = modules.messaging.send_message(attacker.id, dm.id, content)
    assert msg is not None

    notifs = modules.notifications.create_notifications_for_message(
        message_id=msg.id,
        author_id=attacker.id,
        conversation_id=dm.id,
        content=content,
        server_id=None,
        channel_id=None,
    )

    victim_notifs = [n for n in notifs if n.user_id == victim.id]
    assert victim_notifs == [], (
        "a USER mention in a DM leaked a notification carrying the private "
        "DM content preview to a non-participant"
    )


def test_dm_user_mention_notifies_real_participant(modules):
    """Positive control: a mentioned participant is still notified."""
    author = _mkuser(modules, "dmauthor2")
    peer = _mkuser(modules, "dmpeer2")
    outsider = _mkuser(modules, "dmout2")

    dm = modules.messaging.create_dm(author.id, peer.id)

    content = f"hi <@{peer.id}> and <@{outsider.id}>: {PRIVATE_BODY}"
    msg = modules.messaging.send_message(author.id, dm.id, content)
    assert msg is not None

    notifs = modules.notifications.create_notifications_for_message(
        message_id=msg.id,
        author_id=author.id,
        conversation_id=dm.id,
        content=content,
        server_id=None,
        channel_id=None,
    )

    notified = {n.user_id for n in notifs}
    assert peer.id in notified, "a mentioned participant must be notified"
    assert outsider.id not in notified, "a mentioned non-participant must not be"


def test_dm_nonparticipant_gets_no_push_payload_with_private_body(modules, monkeypatch):
    """The push payload is built from ``notification.content_preview``; a
    non-participant must never get a device push carrying the DM's private
    body, while a real participant still does.
    """
    attacker = _mkuser(modules, "dmpusha")
    peer = _mkuser(modules, "dmpushp")
    victim = _mkuser(modules, "dmpushv")

    dm = modules.messaging.create_dm(attacker.id, peer.id)
    content = f"hey <@{peer.id}> <@{victim.id}> private: {PRIVATE_BODY}"
    msg = modules.messaging.send_message(attacker.id, dm.id, content)
    assert msg is not None

    notifs = modules.notifications.create_notifications_for_message(
        message_id=msg.id,
        author_id=attacker.id,
        conversation_id=dm.id,
        content=content,
        server_id=None,
        channel_id=None,
    )

    sent = []

    class _FakePushManager:
        def send_push(self, **kwargs):
            sent.append(kwargs)
            return 1

    monkeypatch.setattr(
        modules.notifications, "_get_push_manager", lambda: _FakePushManager()
    )
    modules.notifications.deliver_push_notifications(notifs)

    victim_pushes = [p for p in sent if p["user_id"] == victim.id]
    assert victim_pushes == [], (
        "non-participant received a device push carrying the private DM body"
    )
    assert not any(PRIVATE_BODY in (p.get("body") or "") for p in victim_pushes)

    peer_pushes = [p for p in sent if p["user_id"] == peer.id]
    assert peer_pushes and PRIVATE_BODY in (peer_pushes[0].get("body") or ""), (
        "a real participant should still get their DM mention push"
    )
