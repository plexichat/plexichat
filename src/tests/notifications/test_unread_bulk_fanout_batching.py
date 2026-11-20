"""Regression: a notification fan-out must not issue one unread query per user.

``create_notifications_bulk`` used to call ``_update_unread_count`` once per
recipient. That helper does a SELECT followed by an UPDATE or INSERT, so a
single role/everyone mention in a large server held the write transaction open
for ``2 * N`` statements. Both counters live on one ``notif_unread`` row per
(user, conversation), so the bump can be resolved with one scoped read plus two
``execute_many`` batches per bounded chunk.

The replacement also makes the increment atomic (``col = col + 1`` in SQL)
instead of a Python read-modify-write, so two workers bumping the same row
cannot overwrite each other. Every statement is scoped by the caller's
conversation and an explicit user-id chunk, so no recipient can be credited
from another user's or another conversation's row.
"""

import pathlib

from src.core.notifications.manager import creator as creator_module
from src.core.notifications.manager.creator import _UNREAD_BULK_CHUNK_SIZE


CONV = 987654321
MANY_USERS = [10_000 + i for i in range(2 * _UNREAD_BULK_CHUNK_SIZE + 7)]


class _CountingDB:
    """Delegate to the real DB while recording emitted statements."""

    def __init__(self, real):
        self._real = real
        self.calls = []

    def fetch_one(self, sql, params):
        self.calls.append(("fetch_one", sql, tuple(params)))
        return self._real.fetch_one(sql, params)

    def fetch_all(self, sql, params):
        self.calls.append(("fetch_all", sql, tuple(params)))
        return self._real.fetch_all(sql, params)

    def execute(self, sql, params, **kwargs):
        self.calls.append(("execute", sql, tuple(params)))
        return self._real.execute(sql, params, **kwargs)

    def execute_many(self, sql, params, **kwargs):
        self.calls.append(("execute_many", sql, list(params)))
        return self._real.execute_many(sql, params, **kwargs)


def _with_spy(manager, call):
    real = manager._db
    spy = _CountingDB(real)
    manager._db = spy
    try:
        result = call()
    finally:
        manager._db = real
    return result, spy


def _unread_calls(spy):
    return [call for call in spy.calls if "notif_unread" in call[1]]


def test_bulk_unread_bump_bounds_round_trips(notification_manager):
    """Before: 2 statements per user. After: bounded per chunk."""
    # "Before": the legacy per-user helper, simulated against a different
    # conversation so the two paths cannot interfere.
    _, before_spy = _with_spy(
        notification_manager,
        lambda: [
            notification_manager._update_unread_count(
                uid, CONV + 1, 42, 99, is_mention=True
            )
            for uid in MANY_USERS
        ],
    )
    # "After": one bounded bulk call.
    _, after_spy = _with_spy(
        notification_manager,
        lambda: notification_manager._bump_unread_counts_bulk(
            MANY_USERS, CONV + 2, 42, 99, is_mention=True
        ),
    )

    before = len(_unread_calls(before_spy))
    after = len(_unread_calls(after_spy))
    assert before == 2 * len(MANY_USERS)
    chunks = (len(MANY_USERS) + _UNREAD_BULK_CHUNK_SIZE - 1) // _UNREAD_BULK_CHUNK_SIZE
    assert after <= 3 * chunks
    assert after * 10 < before, (
        f"expected order-of-magnitude reduction: {before} -> {after}"
    )

    # No unbounded IN clause, and every statement stays conversation-scoped.
    for _kind, sql, params in _unread_calls(after_spy):
        assert sql.count("?") <= _UNREAD_BULK_CHUNK_SIZE + 2
        assert len(params) <= _UNREAD_BULK_CHUNK_SIZE + 2
        assert "conversation_id = ?" in sql or "INSERT OR IGNORE" in sql


def test_bulk_unread_bump_matches_single_row_semantics(notification_manager):
    """Existing rows increment; missing rows are created; duplicates dedupe."""
    conv = 424243
    existing, fresh = 111, 222

    notification_manager._update_unread_count(existing, conv, 77, 88, is_mention=True)
    notification_manager._update_unread_count(existing, conv, 77, 88, is_mention=False)

    # `existing` is deliberately repeated: one user = one increment.
    notification_manager._bump_unread_counts_bulk(
        [existing, fresh, existing], conv, 77, 88, is_mention=True
    )

    rows = notification_manager._db.fetch_all(
        "SELECT user_id, unread_count, mention_count FROM notif_unread "
        "WHERE conversation_id = ?",
        (conv,),
    )
    by_user = {int(row["user_id"]): row for row in rows}
    assert set(by_user) == {existing, fresh}
    assert by_user[existing]["unread_count"] == 3
    assert by_user[existing]["mention_count"] == 2
    assert by_user[fresh]["unread_count"] == 1
    assert by_user[fresh]["mention_count"] == 1


def test_bulk_unread_bump_never_touches_another_conversation(notification_manager):
    """A fan-out for conversation A cannot modify a recipient's row in B."""
    conv_a, conv_b = 5001, 5002
    uid = 333
    notification_manager._update_unread_count(uid, conv_b, None, None, is_mention=True)

    notification_manager._bump_unread_counts_bulk(
        [uid], conv_a, None, None, is_mention=True
    )

    row_b = notification_manager._db.fetch_one(
        "SELECT unread_count, mention_count FROM notif_unread "
        "WHERE user_id = ? AND conversation_id = ?",
        (uid, conv_b),
    )
    assert row_b["unread_count"] == 1
    assert row_b["mention_count"] == 1
    row_a = notification_manager._db.fetch_one(
        "SELECT unread_count, mention_count FROM notif_unread "
        "WHERE user_id = ? AND conversation_id = ?",
        (uid, conv_a),
    )
    assert row_a["unread_count"] == 1
    assert row_a["mention_count"] == 1


def test_create_notifications_bulk_uses_the_batched_bump(
    monkeypatch, notification_manager, messaging_manager, two_users
):
    """The fan-out path must route through the bounded helper."""
    author, recipient = two_users
    conv = messaging_manager.create_group(
        owner_id=author.id,
        name="unread bulk bump",
        participant_ids=[recipient.id],
    )
    message = messaging_manager.send_message(author.id, conv.id, "hello")
    users = [author.id, recipient.id]

    calls = []

    def _record(*args, **kwargs):
        calls.append((args, kwargs))

    monkeypatch.setattr(notification_manager, "_bump_unread_counts_bulk", _record)
    notification_manager.create_notifications_bulk(
        user_ids=users,
        author_id=author.id,
        message_id=message.id,
        conversation_id=conv.id,
        server_id=None,
        channel_id=None,
        thread_id=None,
        mention_types={},
        content_preview="hi",
        created_at=1,
    )

    assert len(calls) == 1
    args, kwargs = calls[0]
    assert list(args[0]) == users
    assert args[1] == conv.id
    assert kwargs["is_mention"] is True


def test_bulk_unread_bump_sql_is_atomic_and_scoped():
    """Guard the multi-node-safe shape of the emitted SQL."""
    source = pathlib.Path(creator_module.__file__).read_text(encoding="utf-8")
    executable = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    assert "unread_count = unread_count + ?" in executable
    assert "mention_count = mention_count + ?" in executable
    assert "WHERE id = ? AND conversation_id = ?" in executable
    assert "INSERT OR IGNORE INTO notif_unread" in executable
