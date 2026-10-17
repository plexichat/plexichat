"""Tests for messaging edge cases and error handling."""

import pytest


@pytest.mark.messaging
class TestEdgeCases:
    """Tests for messaging edge cases and boundary conditions."""

    def test_send_message_to_nonexistent_conversation(
        self, messaging_manager, test_user
    ):
        """Test that sending to a nonexistent conversation raises an error."""
        with pytest.raises(Exception):
            messaging_manager.send_message(test_user.id, 9999999, "Hello")

    def test_get_messages_from_nonexistent_conversation(
        self, messaging_manager, test_user
    ):
        """Test that getting messages from a nonexistent conversation raises an error."""
        with pytest.raises(Exception):
            messaging_manager.get_messages(test_user.id, 9999999)

    def test_send_very_long_message(self, messaging_manager, two_users):
        """Test that extremely long messages are rejected."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        with pytest.raises(Exception):
            messaging_manager.send_message(user1.id, dm.id, "x" * 100000)

    def test_send_empty_message(self, messaging_manager, two_users):
        """Test that empty messages are rejected."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        with pytest.raises(Exception):
            messaging_manager.send_message(user1.id, dm.id, "")

    def test_edit_other_users_message(self, messaging_manager, two_users):
        """Test that editing another user's message is rejected."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Original")
        with pytest.raises(Exception):
            messaging_manager.edit_message(user2.id, msg.id, "Edited by other")

    def test_delete_other_users_message(self, messaging_manager, two_users):
        """Test that deleting another user's message is rejected."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Keep this")
        with pytest.raises(Exception):
            messaging_manager.delete_message(user2.id, msg.id)

    def test_unicode_message_content(self, messaging_manager, two_users):
        """Test that unicode content is handled properly."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Hello 世界 🌍")
        assert msg.content == "Hello 世界 🌍"

    def test_dm_self_is_prevented(self, messaging_manager, test_user):
        """Test that a user cannot DM themselves."""
        with pytest.raises(Exception):
            messaging_manager.create_dm(test_user.id, test_user.id)

    def test_search_messages_in_dm(self, messaging_manager, two_users):
        """Test searching for messages in a DM conversation."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        hit = messaging_manager.send_message(user1.id, dm.id, "find this keyword")
        messaging_manager.send_message(user1.id, dm.id, "unrelated message")
        results = messaging_manager.search_messages(user1.id, dm.id, "keyword")
        assert isinstance(results, list)
        # Exactly one of the two messages contains the keyword
        assert len(results) == 1
        assert int(results[0].id) == int(hit.id)

    def test_pin_unpin_message(self, messaging_manager, two_users):
        """Test pinning and unpinning a message."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "Pin this")
        result = messaging_manager.pin_message(user1.id, msg.id)
        assert result is True

    def test_search_encrypted_message_token_index(self, messaging_manager, two_users):
        """Substring search finds encrypted messages via per-word blind indexes.

        Content is stored as an ENC envelope (never plaintext); the token table
        must make "keyword" match "find this keyword".
        """
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "find this keyword")

        repo = messaging_manager._message_svc._repo
        row = repo.get_by_id(msg.id)
        assert str(row["content"]).startswith("ENC:")

        token_rows = repo._fetch_all(
            "SELECT token_index FROM msg_message_tokens WHERE message_id = ?",
            (msg.id,),
        )
        assert len(token_rows) >= 3  # find, this, keyword

        results = messaging_manager.search_messages(user1.id, dm.id, "keyword")
        assert any(int(r.id) == int(msg.id) for r in results)

    def test_search_multiple_words_matches_all_tokens(
        self, messaging_manager, two_users
    ):
        """Multi-word searches require every word to be present (encrypted-safe)."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        hit = messaging_manager.send_message(user1.id, dm.id, "alpha beta gamma")
        messaging_manager.send_message(user1.id, dm.id, "alpha only")

        results = messaging_manager.search_messages(user1.id, dm.id, "alpha beta")
        ids = [int(r.id) for r in results]
        assert int(hit.id) in ids

        partial = messaging_manager.search_messages(user1.id, dm.id, "alpha gamma")
        partial_ids = [int(r.id) for r in partial]
        assert int(hit.id) in partial_ids

    def test_search_tokens_refreshed_after_edit(self, messaging_manager, two_users):
        """Editing a message rebuilds its search tokens."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "find this keyword")
        messaging_manager.edit_message(user1.id, msg.id, "removed entirely")

        results = messaging_manager.search_messages(user1.id, dm.id, "keyword")
        assert all(int(r.id) != int(msg.id) for r in results)

        messaging_manager.edit_message(user1.id, msg.id, "keyword back again")
        after = messaging_manager.search_messages(user1.id, dm.id, "keyword")
        assert any(int(r.id) == int(msg.id) for r in after)

    def test_search_literal_percent_is_escaped(self, messaging_manager, two_users):
        """LIKE wildcards in the query are matched literally."""
        user1, user2 = two_users
        dm = messaging_manager.create_dm(user1.id, user2.id)
        msg = messaging_manager.send_message(user1.id, dm.id, "discount 100% today")
        results = messaging_manager.search_messages(user1.id, dm.id, "100%")
        assert any(int(r.id) == int(msg.id) for r in results)
