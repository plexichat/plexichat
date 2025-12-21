"""
Tests for reaction limits and pagination.
"""

import pytest
from src.core.reactions import ReactionLimitError


class TestMaxReactionsLimit:
    """Tests for maximum unique reactions per message."""

    def test_max_reactions_limit_enforced(self, db_and_modules):
        """Test that max reactions limit is enforced."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"limit1_{unique_id}",
            email=f"limit1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"limit2_{unique_id}",
            email=f"limit2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Limit test message")

        for i in range(20):
            reactions.add_reaction(user1.id, msg.id, f"emoji_{i}")

        with pytest.raises(ReactionLimitError) as exc_info:
            reactions.add_reaction(user1.id, msg.id, "emoji_21")

        assert exc_info.value.max_allowed == 20
        assert exc_info.value.current == 20

    def test_same_emoji_different_users_allowed(self, db_and_modules):
        """Test that same emoji by different users does not count toward limit."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"same1_{unique_id}",
            email=f"same1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"same2_{unique_id}",
            email=f"same2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Same emoji test")

        for i in range(20):
            reactions.add_reaction(user1.id, msg.id, f"unique_{i}")

        reaction = reactions.add_reaction(user2.id, msg.id, "unique_0")
        assert reaction is not None

    def test_adding_existing_emoji_allowed_at_limit(self, db_and_modules):
        """Test that adding to existing emoji is allowed even at limit."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"exist1_{unique_id}",
            email=f"exist1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"exist2_{unique_id}",
            email=f"exist2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Existing emoji test")

        for i in range(20):
            reactions.add_reaction(user1.id, msg.id, f"existing_{i}")

        reaction = reactions.add_reaction(user2.id, msg.id, "existing_5")
        assert reaction is not None

    def test_remove_and_add_new_at_limit(self, db_and_modules):
        """Test removing a reaction allows adding new one at limit."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"remove1_{unique_id}",
            email=f"remove1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"remove2_{unique_id}",
            email=f"remove2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Remove and add test")

        for i in range(20):
            reactions.add_reaction(user1.id, msg.id, f"removable_{i}")

        reactions.remove_reaction(user1.id, msg.id, "removable_0")

        reaction = reactions.add_reaction(user1.id, msg.id, "new_emoji")
        assert reaction is not None


class TestPaginationLimits:
    """Tests for pagination limits."""

    def test_users_per_page_limit(self, db_and_modules):
        """Test max users per reaction page is enforced."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        users_list = []
        for i in range(3):
            user = auth.register(
                username=f"page_lim{i}_{unique_id}",
                email=f"page_lim{i}_{unique_id}@example.com",
                password="TestPass123!"
            )
            users_list.append(user)

        dm = messaging.create_dm(users_list[0].id, users_list[1].id)
        msg = messaging.send_message(users_list[0].id, dm.id, "Page limit test")

        reactions.add_reaction(users_list[0].id, msg.id, "page_limit")
        reactions.add_reaction(users_list[1].id, msg.id, "page_limit")

        users = reactions.get_reaction_users(
            users_list[0].id, msg.id, "page_limit",
            limit=1000
        )

        assert len(users) <= 100

    def test_pagination_cursor_works(self, db_and_modules):
        """Test pagination cursor returns correct results."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"cursor1_{unique_id}",
            email=f"cursor1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"cursor2_{unique_id}",
            email=f"cursor2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Cursor test")

        reactions.add_reaction(user1.id, msg.id, "cursor_test")
        reactions.add_reaction(user2.id, msg.id, "cursor_test")

        all_users = reactions.get_reaction_users(user1.id, msg.id, "cursor_test")
        assert len(all_users) == 2

        first_user = all_users[0]
        remaining = reactions.get_reaction_users(
            user1.id, msg.id, "cursor_test",
            after_user_id=first_user.user_id
        )

        assert len(remaining) == 1
        assert remaining[0].user_id != first_user.user_id


class TestConfigurableLimits:
    """Tests for configurable limits."""

    def test_default_max_reactions(self, fresh_users_with_dm):
        """Test default max reactions is 20."""
        user1, user2, dm, msg, reactions, relationships = fresh_users_with_dm

        for i in range(20):
            reactions.add_reaction(user1.id, msg.id, f"default_{i}")

        with pytest.raises(ReactionLimitError) as exc_info:
            reactions.add_reaction(user1.id, msg.id, "default_21")

        assert exc_info.value.max_allowed == 20

    def test_limit_error_contains_info(self, db_and_modules):
        """Test ReactionLimitError contains useful information."""
        db, auth, messaging, servers, relationships, reactions = db_and_modules
        import uuid
        unique_id = uuid.uuid4().hex[:8]

        user1 = auth.register(
            username=f"info1_{unique_id}",
            email=f"info1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"info2_{unique_id}",
            email=f"info2_{unique_id}@example.com",
            password="TestPass123!"
        )

        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, "Info test")

        for i in range(20):
            reactions.add_reaction(user1.id, msg.id, f"info_{i}")

        with pytest.raises(ReactionLimitError) as exc_info:
            reactions.add_reaction(user1.id, msg.id, "info_21")

        error = exc_info.value
        assert error.max_allowed == 20
        assert error.current == 20
        assert "20" in str(error)
