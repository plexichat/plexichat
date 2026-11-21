"""Regression tests for batched enrichment in notification listing/delivery.

The notification listing used to resolve the two ``notif_unread`` aggregates
with separate queries, and push delivery enriched every notification in a
batch with its own author-name and unread-badge lookups (an N+1: 2 queries per
notification). Both paths now batch those reads into one query each while
keeping the same user scope and read-state semantics.
"""

import asyncio
import uuid
from types import SimpleNamespace


from src.api.routes import notifications as notif_route


class _QuerySpy:
    """Count reads issued through a manager's database handle."""

    def __init__(self, db):
        self._db = db
        self.queries = []

    def __getattr__(self, name):
        return getattr(self._db, name)

    def fetch_one(self, query, params=None):
        self.queries.append(" ".join(query.split()))
        return self._db.fetch_one(query, params)

    def fetch_all(self, query, params=None):
        self.queries.append(" ".join(query.split()))
        return self._db.fetch_all(query, params)

    def execute(self, query, params=None):
        return self._db.execute(query, params)


def _register(auth, tag):
    suffix = uuid.uuid4().hex[:8]
    return auth.register(
        username=f"batch{tag}_{suffix}",
        email=f"batch{tag}_{suffix}@example.com",
        password="TestPass123!",
    )


def _seed_notifications(auth, messaging, notifications_mod, count=12):
    """Create ``count`` unread user-mention notifications for one recipient."""
    author = _register(auth, "author")
    recipient = _register(auth, "recipient")
    dm = messaging.create_dm(author.id, recipient.id)
    for index in range(count):
        content = f"<@{recipient.id}> batch {index}"
        msg = messaging.send_message(author.id, dm.id, content)
        notifications_mod.create_notifications_for_message(
            author_id=author.id,
            message_id=msg.id,
            conversation_id=dm.id,
            content=content,
        )
    return author, recipient


def test_listing_batches_unread_aggregate_queries(
    monkeypatch,
    auth_manager,
    messaging_manager,
    notification_manager,
    wired_send_path,
):
    """The listing route reads notifications + both unread totals in 2 queries."""
    author, recipient = _seed_notifications(
        auth_manager, messaging_manager, notification_manager, count=12
    )

    real_db = notification_manager._db

    def fake_get_notifications(user_id, limit=20, unread_only=False):
        return notification_manager.get_notifications(
            user_id, limit=limit, unread_only=unread_only
        )

    def fake_get_unread_totals(user_id):
        return notification_manager.get_unread_totals(user_id)

    legacy_calls = []

    def forbidden(*args, **kwargs):
        legacy_calls.append((args, kwargs))
        raise AssertionError("legacy split aggregate query used by listing")

    fake_mod = SimpleNamespace(
        get_notifications=fake_get_notifications,
        get_unread_totals=fake_get_unread_totals,
        get_mention_count=forbidden,
        get_unread_count=forbidden,
    )
    monkeypatch.setattr(notif_route.api, "get_notifications", lambda: fake_mod)

    spy = _QuerySpy(real_db)
    notification_manager._db = spy
    try:
        response = asyncio.run(
            notif_route.get_notifications(
                limit=20,
                unread_only=False,
                current_user=SimpleNamespace(user_id=recipient.id),
            )
        )
    finally:
        notification_manager._db = real_db

    assert len(response.notifications) == 12
    # Read-state semantics: historical mention counter and exact unread-row
    # total are both preserved, page-independent.
    assert response.unread_count == 12
    assert response.total_unread == 12
    assert legacy_calls == []
    # 1 notification SELECT + 1 combined notif_unread aggregate.
    assert len(spy.queries) == 2, spy.queries
    assert sum("FROM notif_unread" in q for q in spy.queries) == 1


def test_unread_totals_match_legacy_split_queries(
    auth_manager, messaging_manager, notification_manager, wired_send_path
):
    """The combined aggregate returns exactly what the two old queries did."""
    author, recipient = _seed_notifications(
        auth_manager, messaging_manager, notification_manager, count=7
    )

    expected_mentions = notification_manager.get_mention_count(recipient.id)
    expected_unread = notification_manager.get_unread_count(recipient.id).unread_count
    totals = notification_manager.get_unread_totals(recipient.id)

    assert totals.mention_count == expected_mentions == 7
    assert totals.unread_count == expected_unread == 7
    assert totals.user_id == recipient.id


def test_push_delivery_batches_per_notification_enrichment(
    auth_manager, messaging_manager, notification_manager, wired_send_path, monkeypatch
):
    """A batch of N notifications costs 2 enrichment queries, not 2N."""
    author, recipient = _seed_notifications(
        auth_manager, messaging_manager, notification_manager, count=12
    )

    sent = []

    class _FakePushManager:
        def send_push(self, **kwargs):
            sent.append(kwargs)
            return 1

    monkeypatch.setattr(
        notification_manager,
        "_get_push_manager",
        lambda: _FakePushManager(),
    )

    notifications = notification_manager.get_notifications(recipient.id, limit=12)
    assert len(notifications) == 12

    # Parity: single-item payload building is the authoritative expectation.
    expected = [notification_manager.prepare_push_payload(n) for n in notifications]

    real_db = notification_manager._db
    spy = _QuerySpy(real_db)
    notification_manager._db = spy
    try:
        delivered = notification_manager.deliver_push_notifications(notifications)
    finally:
        notification_manager._db = real_db

    assert delivered == 12
    assert len(sent) == 12
    assert [s["title"] for s in sent] == [p.title for p in expected]
    assert [s["badge"] for s in sent] == [p.badge_count for p in expected]

    # Scope-preserving batched lookups: one author-name read and one unread
    # aggregate read for the entire batch.
    assert len(spy.queries) == 2, spy.queries
    assert sum("FROM auth_users" in q for q in spy.queries) == 1
    assert sum("FROM notif_unread" in q for q in spy.queries) == 1


def test_push_enrichment_never_crosses_users(
    auth_manager, messaging_manager, notification_manager, wired_send_path, monkeypatch
):
    """Batched author/badge lookups stay scoped to the batch's own ids."""
    author_a, recipient_a = _seed_notifications(
        auth_manager, messaging_manager, notification_manager, count=3
    )
    author_b, recipient_b = _seed_notifications(
        auth_manager, messaging_manager, notification_manager, count=3
    )
    assert recipient_a.id != recipient_b.id

    names = notification_manager._get_push_sender_names([author_a.id, author_b.id])
    assert names[author_a.id].startswith("batchauthor")
    assert names[author_b.id].startswith("batchauthor")

    counts = notification_manager._get_push_unread_mention_counts(
        [recipient_a.id, recipient_b.id]
    )
    assert counts[recipient_a.id] == 3
    assert counts[recipient_b.id] == 3

    # No rows for an unknown id: fail closed to the neutral defaults.
    unknown = 9_999_999_999
    assert notification_manager._get_push_sender_names([unknown]).get(unknown) is None
    assert (
        notification_manager._get_push_unread_mention_counts([unknown]).get(unknown, 0)
        == 0
    )
