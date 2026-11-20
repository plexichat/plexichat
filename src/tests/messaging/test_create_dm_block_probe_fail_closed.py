"""Regression tests: ``create_dm`` must fail closed on a block when the
optional relationships module is unavailable.

``ConversationService.create_dm`` falls back to a direct ``rel_blocked``
probe when ``_get_relationships_module()`` yields nothing (partial outage of
the relationships module). That probe used to swallow its own failure and set
``blocked_row = None``, silently skipping the block check: during a database
hiccup a blocked pair could open a *fresh* DM even though the send path
(``_enforce_dm_block_rules``) denies the same pair. The fix makes the probe
fail closed, matching the send path's contract.

Run with: pytest src/tests/messaging/test_create_dm_block_probe_fail_closed.py -q
"""

import pytest

import src.core.messaging.services.conversation as conversation_service
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
class TestCreateDmBlockProbeFailClosed:
    def test_block_enforced_when_relationships_module_is_unavailable(
        self, db, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        """A block row is authoritative even with no relationships module."""
        user1, user2 = two_users
        wired_relationships.block_user(user2.id, user1.id)

        monkeypatch.setattr(
            conversation_service, "_get_relationships_module", lambda: None
        )

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_dm(user1.id, user2.id)

    def test_no_block_still_creates_dm_when_module_is_unavailable(
        self, db, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        """The fail-closed gate must not break legitimate unblocked DMs."""
        user1, user2 = two_users

        monkeypatch.setattr(
            conversation_service, "_get_relationships_module", lambda: None
        )

        dm = messaging_manager.create_dm(user1.id, user2.id)
        assert dm is not None

    def test_failed_block_probe_denies_dm_creation(
        self, db, messaging_manager, wired_relationships, two_users, monkeypatch
    ):
        """If the authoritative probe itself fails, deny rather than guess."""
        user1, user2 = two_users
        real_fetch_one = messaging_manager._db.fetch_one

        def _broken_fetch_one(query, *args, **kwargs):
            # Fail only the authoritative block probe; leave every other
            # lookup intact so the test reaches the fallback branch.
            if "rel_blocked" in str(query):
                raise RuntimeError("database unavailable")
            return real_fetch_one(query, *args, **kwargs)

        monkeypatch.setattr(
            conversation_service, "_get_relationships_module", lambda: None
        )
        monkeypatch.setattr(messaging_manager._db, "fetch_one", _broken_fetch_one)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.create_dm(user1.id, user2.id)
