"""Regression: deleting a notification must decrement the badge at most once.

``notif_unread`` is a mutable mirror of the user's unread notification rows.
``delete_notification`` validates ownership with a fetch, then issues a bare
``DELETE`` and -- from the *stale* in-memory snapshot -- decrements both
counters whenever the fetched row was unread.

That leaves a TOCTOU window between the fetch and the DELETE:

  1. Request A fetches notification N and sees ``read = 0``.
  2. Request B marks the same N read (``mark_notification_read``) and
     decrements the counters once.
  3. A's DELETE still lands -- the row exists, it is just ``read = 1`` now.
     A trusts its snapshot and decrements a *second* time.

One notification therefore subtracted twice from the counter, so the badge
under-reports (a stale, too-low count). The inverse shape -- two deletes of the
same row -- must likewise never subtract twice.

The fix must make the decrement conditional on A's *own* state transition at
DELETE time (guard on ``read = 0``), not on a snapshot taken before it.
"""

import pytest

from src.core.notifications.exceptions import NotificationNotFoundError

pytestmark = [pytest.mark.notifications, pytest.mark.security]


def _seed(notification_manager, db, messaging_manager, two_users):
    """Two unread notifications for one user, with a matching 2/2 mirror."""
    author, recipient = two_users
    dm = messaging_manager.create_dm(author.id, recipient.id)
    uid = recipient.id
    conv = dm.id

    notif_ids = []
    for i in range(2):
        msg = messaging_manager.send_message(author.id, conv, f"ping {i}")
        nid = msg.id + 1_000_000
        db.execute(
            "INSERT INTO notif_notifications "
            "(id, user_id, sender_id, message_id, conversation_id, mention_type, "
            "content_preview, read, created_at) VALUES (?, ?, ?, ?, ?, 'user', 'hi', 0, ?)",
            (nid, uid, author.id, msg.id, conv, 1000),
        )
        notif_ids.append(nid)

    db.execute(
        "INSERT INTO notif_unread "
        "(id, user_id, conversation_id, unread_count, mention_count, updated_at) "
        "VALUES (?, ?, ?, 2, 2, 1000)",
        (9_000_001, uid, conv),
    )
    totals = notification_manager.get_unread_totals(uid)
    assert (totals.unread_count, totals.mention_count) == (2, 2)
    return uid, conv, notif_ids


def test_concurrent_mark_read_then_delete_decrements_once(
    notification_manager, db, messaging_manager, two_users
):
    """A mark-read racing a delete must not subtract the same row twice."""
    uid, conv, (n1, _n2) = _seed(notification_manager, db, messaging_manager, two_users)

    real_execute = db.execute
    fired = {"n": 0}

    def racing_execute(query, params=(), **kwargs):
        if fired["n"] == 0 and query.lstrip().upper().startswith(
            "DELETE FROM NOTIF_NOTIFICATIONS"
        ):
            fired["n"] += 1
            # Interleave: between A's ownership fetch and A's DELETE, request B
            # marks the very same notification read and decrements the mirror
            # once (exactly what mark_notification_read does today).
            real_execute(
                "UPDATE notif_notifications SET read = 1 WHERE id = ? AND user_id = ?",
                (n1, uid),
            )
            real_execute(
                "UPDATE notif_unread "
                "SET unread_count = unread_count - 1, mention_count = mention_count - 1 "
                "WHERE user_id = ? AND conversation_id = ?",
                (uid, conv),
            )
            # A's DELETE still lands: the row exists, it is merely read now.
        return real_execute(query, params, **kwargs)

    db.execute = racing_execute
    try:
        assert notification_manager.delete_notification(uid, n1) is True
    finally:
        db.execute = real_execute

    assert fired["n"] >= 1

    # Exactly one notification remains unread (the second one), so the badge
    # must be 1/1. The buggy snapshot-based decrement makes it 0/0 -- stale.
    totals = notification_manager.get_unread_totals(uid)
    assert totals.unread_count == 1, (
        f"badge under-counted after concurrent mark-read + delete: {totals.unread_count}"
    )
    assert totals.mention_count == 1, (
        f"mention badge under-counted after concurrent mark-read + delete: {totals.mention_count}"
    )
    per_conv = notification_manager.get_unread_counts(uid)
    assert per_conv[conv].unread_count == 1
    assert per_conv[conv].mention_count == 1


def test_two_concurrent_deletes_decrement_once(
    notification_manager, db, messaging_manager, two_users
):
    """Two deletes racing on the same unread row must decrement it only once."""
    uid, conv, (n1, _n2) = _seed(notification_manager, db, messaging_manager, two_users)

    real_execute = db.execute
    fired = {"n": 0}

    def racing_execute(query, params=(), **kwargs):
        if fired["n"] == 0 and query.lstrip().upper().startswith(
            "DELETE FROM NOTIF_NOTIFICATIONS"
        ):
            fired["n"] += 1
            # A second request deletes the same row and decrements first.
            real_execute(
                "DELETE FROM notif_notifications WHERE id = ? AND user_id = ?",
                (n1, uid),
            )
            real_execute(
                "UPDATE notif_unread "
                "SET unread_count = unread_count - 1, mention_count = mention_count - 1 "
                "WHERE user_id = ? AND conversation_id = ?",
                (uid, conv),
            )
        return real_execute(query, params, **kwargs)

    db.execute = racing_execute
    try:
        try:
            notification_manager.delete_notification(uid, n1)
        except NotificationNotFoundError:
            # The competing request already removed the row; that is a valid
            # outcome. What matters is that the badge was not decremented twice.
            pass
    finally:
        db.execute = real_execute

    assert fired["n"] == 1
    totals = notification_manager.get_unread_totals(uid)
    assert (totals.unread_count, totals.mention_count) == (1, 1), (
        "two deletes of one row must not double-decrement the badge"
    )
