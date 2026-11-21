"""Regression tests: DM sends must fail closed on a block, even when the
optional relationships module is unavailable.

``_enforce_dm_block_rules`` used to ``return`` immediately when
``_get_relationships_module()`` yielded nothing. A partial outage therefore
reopened legacy DM channels to a blocked pair: either party could keep writing
into the DM even though ``create_dm``/typing already treat the block as
authoritative in that state. The fix falls back to a direct ``rel_blocked``
probe (and denies if that probe fails).

Run with: pytest src/tests/messaging/test_send_dm_block_fail_closed.py -q
"""

import pytest

import src.core.messaging.services.message.send as send_service
from src.core.messaging.exceptions import ConversationAccessDeniedError


@pytest.fixture
def wired_relationships(db):
    """Expose a real relationships manager, then restore global state."""
    from src.core import relationships

    previous_manager = relationships._manager
    previous_setup = relationships._setup_complete
    relationships.setup(db)
    try:
        yield relationships
    finally:
        relationships._manager = previous_manager
        relationships._setup_complete = previous_setup


@pytest.mark.messaging
class TestSendDmBlockFailClosed:
    def test_block_enforced_when_relationships_module_is_unavailable(
        self, db, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        user1, user2 = two_users
        # Create the legacy DM BEFORE blocking (create_dm also honors blocks).
        dm = messaging_manager.create_dm(user1.id, user2.id)
        wired_relationships.block_user(user2.id, user1.id)

        # Simulate the optional relationships module failing to initialise.
        # The block row is still authoritative in the database.
        monkeypatch.setattr(send_service, "_get_relationships_module", lambda: None)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(user1.id, dm.id, "still talking")

    def test_no_block_still_allows_send_when_module_is_unavailable(
        self, db, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        """The fail-closed gate must not break legitimate unblocked DMs."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        monkeypatch.setattr(send_service, "_get_relationships_module", lambda: None)

        msg = messaging_manager.send_message(user1.id, dm.id, "hello")
        assert msg is not None

    def test_failed_block_probe_denies_the_send(
        self, db, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        """If the authoritative probe itself fails, deny rather than guess."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)

        real_fetch_one = messaging_manager._db.fetch_one

        def _broken_fetch_one(query, *args, **kwargs):
            # Fail only the authoritative block probe; leave the preceding
            # conversation lookup intact so the test reaches the fallback.
            if "rel_blocked" in str(query):
                raise RuntimeError("database unavailable")
            return real_fetch_one(query, *args, **kwargs)

        monkeypatch.setattr(send_service, "_get_relationships_module", lambda: None)
        monkeypatch.setattr(messaging_manager._db, "fetch_one", _broken_fetch_one)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(user1.id, dm.id, "hello")
