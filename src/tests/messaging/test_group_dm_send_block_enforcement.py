"""A block must also close the GROUP-DM *send* path.

``_enforce_dm_block_rules`` only acts on ``ConversationType.DM`` rows and
returns early for every other conversation type. Group DMs are ``GROUP``
rows, so once two users are already participants of a group DM, a block
added afterwards leaves that group DM writable by either blocked party.
This is the send-side twin of the group-DM insertion gates peers added:
insertion is closed, but a pair that was already co-resident before the
block can keep delivering messages into the same group DM.

These tests are written against the desired (fail-closed) behaviour, so
they FAIL on the unfixed code and PASS once the gate covers group DMs.
Run with: pytest src/tests/messaging/test_group_dm_send_block_enforcement.py -q
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
class TestGroupDmSendBlockEnforcement:
    def test_block_closes_group_dm_send(
        self, messaging_manager, wired_relationships, three_users
    ):
        owner, attacker, victim = three_users
        # Create the group while both are still on speaking terms: create_group
        # itself rejects a blocked pair, so the block must come afterwards.
        group = messaging_manager.create_group(
            owner.id, "Group", [attacker.id, victim.id]
        )

        wired_relationships.block_user(victim.id, attacker.id)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(attacker.id, group.id, "still talking")

    def test_block_closes_group_dm_send_when_relationships_unavailable(
        self, messaging_manager, wired_relationships, three_users, monkeypatch
    ):
        owner, attacker, victim = three_users
        group = messaging_manager.create_group(
            owner.id, "Group", [attacker.id, victim.id]
        )
        wired_relationships.block_user(victim.id, attacker.id)

        # The authoritative rel_blocked row survives a relationships outage;
        # the fallback probe must still deny the group-DM send.
        monkeypatch.setattr(send_service, "_get_relationships_module", lambda: None)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(attacker.id, group.id, "still talking")

    def test_failed_block_probe_denies_group_dm_send(
        self, messaging_manager, wired_relationships, three_users, monkeypatch
    ):
        owner, attacker, victim = three_users
        group = messaging_manager.create_group(
            owner.id, "Group", [attacker.id, victim.id]
        )
        wired_relationships.block_user(victim.id, attacker.id)

        real_fetch_one = messaging_manager._db.fetch_one

        def _broken_fetch_one(query, *args, **kwargs):
            if "rel_blocked" in str(query):
                raise RuntimeError("database unavailable")
            return real_fetch_one(query, *args, **kwargs)

        monkeypatch.setattr(send_service, "_get_relationships_module", lambda: None)
        monkeypatch.setattr(messaging_manager._db, "fetch_one", _broken_fetch_one)

        with pytest.raises(ConversationAccessDeniedError):
            messaging_manager.send_message(attacker.id, group.id, "still talking")

    def test_unblocked_group_dm_send_still_works(
        self, messaging_manager, wired_relationships, three_users
    ):
        """Control: the gate must not break a legitimate group-DM send."""
        owner, member, other = three_users
        group = messaging_manager.create_group(owner.id, "Group", [member.id, other.id])

        msg = messaging_manager.send_message(member.id, group.id, "hello")
        assert msg is not None

    def test_server_channel_group_row_is_exempt_from_dm_block_gate(
        self, messaging_manager, wired_relationships, server_with_channel
    ):
        """Server channels are GROUP rows with metadata.server_id; posting to
        them is governed by server membership / ``messages.send`` at the REST
        layer, not DM privacy. The core gate must stay scoped to (group) DMs,
        so a block between two members must not deny their channel sends.
        """
        owner, member1, member2, server, channel, server_manager, _tm = (
            server_with_channel
        )
        wired_relationships.block_user(member1.id, member2.id)

        msg = messaging_manager.send_message(
            member1.id, channel.conversation_id, "hello server"
        )
        assert msg is not None
