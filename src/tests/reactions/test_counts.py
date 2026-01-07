"""
Tests for reaction counts and user lists.
"""


class TestReactionCounts:
    """Tests for reaction count aggregation."""

    def test_get_reactions_empty(self, fresh_users_with_dm):
        """Test getting reactions on message with no reactions."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        msg_reactions = reactions.get_reactions(user1.id, msg.id)

        assert msg_reactions.message_id == msg.id
        assert len(msg_reactions.reactions) == 0
        assert msg_reactions.total_count == 0

    def test_get_reactions_single(self, fresh_users_with_dm):
        """Test getting single reaction."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "single")

        msg_reactions = reactions.get_reactions(user1.id, msg.id)

        assert len(msg_reactions.reactions) == 1
        assert msg_reactions.reactions[0].emoji == "single"
        assert msg_reactions.reactions[0].count == 1
        assert msg_reactions.reactions[0].me is True
        assert msg_reactions.total_count == 1

    def test_get_reactions_multiple_users_same_emoji(self, fresh_users_with_dm):
        """Test count when multiple users react with same emoji."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "popular")
        reactions.add_reaction(user2.id, msg.id, "popular")

        msg_reactions = reactions.get_reactions(user1.id, msg.id)

        popular = next(
            (r for r in msg_reactions.reactions if r.emoji == "popular"), None
        )
        assert popular is not None
        assert popular.count == 2
        assert popular.me is True

        msg_reactions_u2 = reactions.get_reactions(user2.id, msg.id)
        popular_u2 = next(
            (r for r in msg_reactions_u2.reactions if r.emoji == "popular"), None
        )
        assert popular_u2 is not None
        assert popular_u2.me is True

    def test_get_reactions_multiple_emojis(self, fresh_users_with_dm):
        """Test counts for multiple different emojis."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "emoji_a")
        reactions.add_reaction(user1.id, msg.id, "emoji_b")
        reactions.add_reaction(user2.id, msg.id, "emoji_a")

        msg_reactions = reactions.get_reactions(user1.id, msg.id)

        assert len(msg_reactions.reactions) == 2
        assert msg_reactions.total_count == 3

        emoji_a = next(
            (r for r in msg_reactions.reactions if r.emoji == "emoji_a"), None
        )
        emoji_b = next(
            (r for r in msg_reactions.reactions if r.emoji == "emoji_b"), None
        )

        assert emoji_a is not None
        assert emoji_b is not None
        assert emoji_a.count == 2
        assert emoji_b.count == 1

    def test_me_field_false_when_not_reacted(self, fresh_users_with_dm):
        """Test 'me' field is False when user has not reacted."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "not_me")

        msg_reactions = reactions.get_reactions(user2.id, msg.id)

        not_me = next((r for r in msg_reactions.reactions if r.emoji == "not_me"), None)
        assert not_me is not None
        assert not_me.me is False

    def test_reactions_ordered_by_first_added(self, fresh_users_with_dm):
        """Test reactions are ordered by when first added."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "first")
        reactions.add_reaction(user1.id, msg.id, "second")
        reactions.add_reaction(user1.id, msg.id, "third")

        msg_reactions = reactions.get_reactions(user1.id, msg.id)

        emojis = [r.emoji for r in msg_reactions.reactions]
        assert emojis == ["first", "second", "third"]


class TestReactionUsers:
    """Tests for getting users who reacted."""

    def test_get_reaction_users_empty(self, fresh_users_with_dm):
        """Test getting users for emoji with no reactions."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        users = reactions.get_reaction_users(user1.id, msg.id, "nonexistent")

        assert len(users) == 0

    def test_get_reaction_users_single(self, fresh_users_with_dm):
        """Test getting single user who reacted."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "single_user")

        users = reactions.get_reaction_users(user1.id, msg.id, "single_user")

        assert len(users) == 1
        assert users[0].user_id == user1.id
        assert users[0].reacted_at > 0

    def test_get_reaction_users_multiple(self, fresh_users_with_dm):
        """Test getting multiple users who reacted."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "multi_user")
        reactions.add_reaction(user2.id, msg.id, "multi_user")

        users = reactions.get_reaction_users(user1.id, msg.id, "multi_user")

        assert len(users) == 2
        user_ids = [u.user_id for u in users]
        assert user1.id in user_ids
        assert user2.id in user_ids

    def test_get_reaction_users_pagination(self, db_and_modules):
        """Test pagination of reaction users."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid

        unique_id = uuid.uuid4().hex[:8]

        users_list = []
        for i in range(5):
            user = auth.register(
                username=f"page_user{i}_{unique_id}",
                email=f"page_user{i}_{unique_id}@example.com",
                password="TestPass123!",
            )
            users_list.append(user)

        dm = messaging.create_dm(users_list[0].id, users_list[1].id)
        msg = messaging.send_message(users_list[0].id, dm.id, "Pagination test")

        for user in users_list[:2]:
            reactions.add_reaction(user.id, msg.id, "paginate")

        page1 = reactions.get_reaction_users(
            users_list[0].id, msg.id, "paginate", limit=1
        )
        assert len(page1) == 1

        page2 = reactions.get_reaction_users(
            users_list[0].id,
            msg.id,
            "paginate",
            limit=1,
            after_user_id=page1[0].user_id,
        )
        assert len(page2) == 1
        assert page2[0].user_id != page1[0].user_id

    def test_get_reaction_users_limit(self, fresh_users_with_dm):
        """Test limit parameter for reaction users."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "limit_test")
        reactions.add_reaction(user2.id, msg.id, "limit_test")

        users = reactions.get_reaction_users(user1.id, msg.id, "limit_test", limit=1)

        assert len(users) == 1


class TestUserReactions:
    """Tests for getting a user's reactions on a message."""

    def test_get_user_reactions_empty(self, fresh_users_with_dm):
        """Test getting reactions when user has not reacted."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        user_reactions = reactions.get_user_reactions(user1.id, msg.id)

        assert len(user_reactions) == 0

    def test_get_user_reactions_single(self, fresh_users_with_dm):
        """Test getting single reaction by user."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "my_reaction")

        user_reactions = reactions.get_user_reactions(user1.id, msg.id)

        assert len(user_reactions) == 1
        assert user_reactions[0].emoji == "my_reaction"

    def test_get_user_reactions_multiple(self, fresh_users_with_dm):
        """Test getting multiple reactions by user."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "my_r1")
        reactions.add_reaction(user1.id, msg.id, "my_r2")
        reactions.add_reaction(user1.id, msg.id, "my_r3")

        user_reactions = reactions.get_user_reactions(user1.id, msg.id)

        assert len(user_reactions) == 3
        emojis = [r.emoji for r in user_reactions]
        assert "my_r1" in emojis
        assert "my_r2" in emojis
        assert "my_r3" in emojis

    def test_get_user_reactions_excludes_others(self, fresh_users_with_dm):
        """Test user reactions excludes other users' reactions."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        reactions.add_reaction(user1.id, msg.id, "user1_only")
        reactions.add_reaction(user2.id, msg.id, "user2_only")

        user1_reactions = reactions.get_user_reactions(user1.id, msg.id)

        emojis = [r.emoji for r in user1_reactions]
        assert "user1_only" in emojis
        assert "user2_only" not in emojis
