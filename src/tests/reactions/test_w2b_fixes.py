"""
W2-B regression tests for reaction fixes.

H1: moderator wipes must fail closed for soft-deleted conversations.
H2: server permission gate must fail closed when servers module unwired.
M1: arbitrary ASCII text is rejected as emoji.
M4: count APIs apply the same blocked-user filtering as list APIs.
L4: event fan-out only reaches members who can view the channel.
"""

import pytest
import uuid

from src.core.reactions import (
    InvalidEmojiError,
    MessageNotFoundError,
    PermissionDeniedError,
)


class TestW2BFixes:
    def _reactions(self, modules):
        return modules.reactions

    def test_h1_soft_deleted_conversation_denies_wipe(self, modules, test_db):
        reactions = self._reactions(modules)
        user1 = modules.auth.register(
            username=f"h1own_{uuid.uuid4().hex[:8]}",
            email=f"h1own_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        user2 = modules.auth.register(
            username=f"h1oth_{uuid.uuid4().hex[:8]}",
            email=f"h1oth_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        outsider = modules.auth.register(
            username=f"h1out_{uuid.uuid4().hex[:8]}",
            email=f"h1out_{uuid.uuid4().hex[:8]}@example.com",
            password="TestPass123!",
        )
        dm = modules.messaging.create_dm(user1.id, user2.id)
        msg = modules.messaging.send_message(user1.id, dm.id, "wipe me")
        reactions.add_reaction(user1.id, msg.id, ":h1check:")

        test_db.execute(
            "UPDATE msg_conversations SET deleted = 1 WHERE id = ?", (dm.id,)
        )

        # Owner can no longer wipe (permission block previously skipped).
        with pytest.raises(PermissionDeniedError):
            reactions.remove_all_reactions(user1.id, msg.id)

        # Non-participant gets the standard not-found response.
        with pytest.raises(MessageNotFoundError):
            reactions.remove_all_reactions(outsider.id, msg.id)

        with pytest.raises(PermissionDeniedError):
            reactions.remove_all_reactions_for_emoji(user1.id, msg.id, ":h1check:")

        # The reaction survived: fail-closed means no unauthorized write.
        assert reactions.get_reaction_count(msg.id) == 1

    def test_h2_unwired_servers_denies_server_channel_react(
        self, auth_manager, messaging_manager, server_manager, test_db, reaction_manager
    ):
        unique_id = uuid.uuid4().hex[:8]
        owner = auth_manager.register(
            username=f"h2own_{unique_id}",
            email=f"h2own_{unique_id}@example.com",
            password="TestPass123!",
        )
        server = server_manager.create_server(owner.id, f"H2 Server {unique_id}")
        channel = server_manager.create_channel(owner.id, server.id, "general", "text")
        msg = messaging_manager.send_message(
            owner.id, channel.conversation_id, "in channel"
        )

        # Simulate a deployment that booted WITHOUT the servers module.
        reaction_manager._servers = None

        with pytest.raises(PermissionDeniedError):
            reaction_manager.add_reaction(owner.id, msg.id, ":h2check:")

        with pytest.raises(PermissionDeniedError):
            reaction_manager.remove_all_reactions(owner.id, msg.id)

        with pytest.raises(PermissionDeniedError):
            reaction_manager.remove_all_reactions_for_emoji(
                owner.id, msg.id, ":h2check:"
            )

    def test_f2_unwired_servers_does_not_fan_out_to_unverified_members(
        self, auth_manager, messaging_manager, server_manager, reaction_manager
    ):
        """Agent 21 F2: participant fan-out must fail CLOSED without servers.

        ``_check_server_permission`` already denies actions it cannot verify
        when the servers module is unwired; the event fan-out must apply the
        same rule instead of broadcasting reactions to every unverified
        server member.
        """
        unique_id = uuid.uuid4().hex[:8]
        owner = auth_manager.register(
            username=f"f2own_{unique_id}",
            email=f"f2own_{unique_id}@example.com",
            password="TestPass123!",
        )
        member = auth_manager.register(
            username=f"f2mem_{unique_id}",
            email=f"f2mem_{unique_id}@example.com",
            password="TestPass123!",
        )
        server = server_manager.create_server(owner.id, f"F2 Server {unique_id}")
        server_manager.add_member(server.id, member.id)
        channel = server_manager.create_channel(owner.id, server.id, "general", "text")

        original_servers = reaction_manager._servers
        try:
            # Sanity: with servers wired, a server member who can view the
            # channel is part of the fan-out.
            reaction_manager._servers = server_manager
            wired = reaction_manager.get_participant_ids(channel.conversation_id)
            assert member.id in wired

            # Simulate a deployment that booted WITHOUT the servers module:
            # membership can no longer be verified, so nobody is added.
            reaction_manager._servers = None
            unwired = reaction_manager.get_participant_ids(channel.conversation_id)
            # The unverified server member is dropped; explicitly stored
            # conversation participants (the owner) are still included.
            assert member.id not in unwired
            assert owner.id in unwired
        finally:
            reaction_manager._servers = original_servers

    def test_m4_counts_filter_blocked_users(
        self, auth_manager, messaging_manager, reaction_manager, rel_manager
    ):
        unique_id = uuid.uuid4().hex[:8]
        users = []
        for tag in ("m4a", "m4b", "m4c"):
            users.append(
                auth_manager.register(
                    username=f"{tag}_{unique_id}",
                    email=f"{tag}_{unique_id}@example.com",
                    password="TestPass123!",
                )
            )
        group = messaging_manager.create_group(
            users[0].id, f"M4 Group {unique_id}", [users[1].id, users[2].id]
        )
        msg = messaging_manager.send_message(users[0].id, group.id, "count me")

        for u in users:
            reaction_manager.add_reaction(u.id, msg.id, "👍")

        rel_manager.block_user(users[0].id, users[1].id)

        # Viewer-filtered counts agree with the filtered list API...
        visible = reaction_manager.get_reactions(users[0].id, msg.id)
        assert (
            reaction_manager.get_reaction_count(msg.id, viewer_id=users[0].id)
            == visible.total_count
        )
        assert (
            reaction_manager.get_reaction_count_by_emoji(
                msg.id, "👍", viewer_id=users[0].id
            )
            == visible.reactions[0].count
        )

        # ...and both hide the blocked user's reaction.
        assert visible.total_count == 2
        assert reaction_manager.get_reaction_count(msg.id, viewer_id=users[0].id) == 2
        assert (
            reaction_manager.get_reaction_count_by_emoji(
                msg.id, "👍", viewer_id=users[0].id
            )
            == 2
        )

        # Unfiltered callers still see raw totals.
        assert reaction_manager.get_reaction_count(msg.id) == 3

    def test_m4_legacy_junk_rows_read_gracefully(self, reaction_manager, test_db):
        test_db.execute(
            "INSERT INTO auth_users (id, account_type, username, email_index, password_hash, permissions, created_at, updated_at) VALUES (1, 'user', 'user1', 'u1@t.com', 'hash', '[]', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_conversations (id, conversation_type, created_at, updated_at) VALUES (1, 'dm', 1000, 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_participants (id, conversation_id, user_id, role, joined_at) VALUES (1, 1, 1, 'member', 1000)"
        )
        test_db.execute(
            "INSERT INTO msg_messages (id, conversation_id, author_id, content, created_at, updated_at, message_type) VALUES (1, 1, 1, 'legacy', 1000, 1000, 'text')"
        )
        test_db.execute(
            "INSERT INTO react_reactions (id, message_id, user_id, emoji, is_custom, custom_emoji_id, created_at) VALUES (1, 1, 1, 'javascript:alert', 0, NULL, 1000)"
        )

        # Reads over legacy junk rows degrade gracefully instead of raising.
        assert reaction_manager.get_reaction_users(1, 1, "javascript:alert") == []
        assert reaction_manager.get_reaction_count_by_emoji(1, "javascript:alert") == 0

    def test_m1_validator_rejects_arbitrary_ascii(self, reaction_manager):
        for junk in ["javascript:alert", "http:/evil", "admin:root", "do-not-trust"]:
            with pytest.raises(InvalidEmojiError):
                reaction_manager._validate_emoji(junk)

    def test_l4_participant_fanout_filters_channel_visibility(
        self, auth_manager, messaging_manager, server_manager, test_db, reaction_manager
    ):
        unique_id = uuid.uuid4().hex[:8]
        owner = auth_manager.register(
            username=f"l4own_{unique_id}",
            email=f"l4own_{unique_id}@example.com",
            password="TestPass123!",
        )
        member = auth_manager.register(
            username=f"l4mem_{unique_id}",
            email=f"l4mem_{unique_id}@example.com",
            password="TestPass123!",
        )
        hidden = auth_manager.register(
            username=f"l4hid_{unique_id}",
            email=f"l4hid_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = server_manager.create_server(owner.id, f"L4 Server {unique_id}")
        server_manager.add_member(server.id, member.id)
        server_manager.add_member(server.id, hidden.id)
        channel = server_manager.create_channel(owner.id, server.id, "secret", "text")

        # Wire the servers module so channel-visibility filtering applies.
        reaction_manager._servers = server_manager

        original = server_manager.has_permission

        def denying_has_permission(uid, sid, perm, cid=None):
            if perm == "messages.read" and uid == hidden.id:
                return False
            return original(uid, sid, perm, cid)

        server_manager.has_permission = denying_has_permission
        try:
            recipient_ids = reaction_manager.get_participant_ids(
                channel.conversation_id
            )
        finally:
            server_manager.has_permission = original

        assert owner.id in recipient_ids
        assert member.id in recipient_ids
        assert hidden.id not in recipient_ids
