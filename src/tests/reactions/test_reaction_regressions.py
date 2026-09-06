"""
Regression tests for reactions cluster fixes.

Covers:
- Custom-emoji table migration runs at manager construction (M2)
- remove_reaction maps invalid emoji to 400, not 500 (M5)
- Blocked-user SQL parameters are deterministically ordered (L6)
- get_participant_ids handles NULL/missing metadata consistently (L7)
"""

import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from src.core.reactions import InvalidEmojiError


class TestEmojiTableMigration:
    """M2: the custom-emoji schema migration must actually run."""

    def test_migration_runs_at_construction(self, test_db):
        from src.core.reactions.manager import ReactionManager

        with patch.object(ReactionManager, "_migrate_emoji_table") as migrate:
            ReactionManager(test_db)
        migrate.assert_called_once()

    def test_migration_adds_columns_to_legacy_table(self, test_db):
        # Simulate a table created before url/size/width/height/created_by/
        # available existed: INSERTs with those columns would have failed.
        test_db.execute("DROP TABLE IF EXISTS react_custom_emoji")
        test_db.execute(
            """
            CREATE TABLE react_custom_emoji (
                id INTEGER PRIMARY KEY,
                server_id INTEGER,
                name TEXT,
                animated INTEGER
            )
            """
        )

        from src.core.reactions.manager import ReactionManager

        ReactionManager(test_db)

        # The migration must have added the columns: selecting them works.
        row = test_db.fetch_one(
            "SELECT url, size, available FROM react_custom_emoji LIMIT 1"
        )
        assert row is None or "url" in row

    def test_migration_idempotent(self, test_db):
        from src.core.reactions.manager import ReactionManager

        # First construction runs it; a second must not raise.
        ReactionManager(test_db)
        ReactionManager(test_db)

        row = test_db.fetch_one(
            "SELECT url, size, available FROM react_custom_emoji LIMIT 1"
        )
        assert row is None or "url" in row


class TestRemoveReactionInvalidEmoji:
    """M5: invalid emoji on DELETE /reactions must be a 400, not a 500."""

    @pytest.mark.asyncio
    async def test_remove_reaction_invalid_emoji_returns_400(self):
        from src.api.routes.reactions import remove_reaction

        fake = MagicMock()
        fake.remove_reaction.side_effect = InvalidEmojiError("bad emoji")
        request = SimpleNamespace(state=SimpleNamespace(is_internal=False))
        current_user = SimpleNamespace(user_id=1)
        with (
            patch("src.api.get_reactions", return_value=fake),
            patch(
                "src.api.routes.reactions.ratelimit.check_rate_limit",
                return_value=SimpleNamespace(allowed=True),
            ),
            patch(
                "src.api.routes.reactions._validate_message_in_channel",
                return_value=5,
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await remove_reaction("5", "5", "bad", request, current_user)

        assert exc.value.status_code == 400
        assert "Invalid emoji" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_remove_reaction_valid_path_still_succeeds(self):
        from src.api.routes.reactions import remove_reaction

        fake = MagicMock()
        request = SimpleNamespace(state=SimpleNamespace(is_internal=False))
        current_user = SimpleNamespace(user_id=1)
        with (
            patch("src.api.get_reactions", return_value=fake),
            patch(
                "src.api.routes.reactions.ratelimit.check_rate_limit",
                return_value=SimpleNamespace(allowed=True),
            ),
            patch(
                "src.api.routes.reactions._validate_message_in_channel",
                return_value=5,
            ),
            # Drop the fire-and-forget dispatch coroutine instead of
            # scheduling it: a bare MagicMock return would leave the
            # coroutine unawaited (RuntimeWarning) and could mask real
            # dispatch errors as passes.
            patch(
                "asyncio.create_task",
                side_effect=lambda coro: coro.close(),
            ),
        ):
            response = await remove_reaction(
                "5", "5", "%F0%9F%91%8D", request, current_user
            )

        assert response.success is True


class TestDeterministicBlockedParams:
    """L6: blocked-user placeholders must be in a deterministic order."""

    def test_blocked_user_params_sorted_and_stable(
        self, auth_manager, messaging_manager, reaction_manager, rel_manager, test_db
    ):
        unique_id = uuid.uuid4().hex[:8]
        users = []
        for tag in ("l6a", "l6b", "l6c"):
            users.append(
                auth_manager.register(
                    username=f"{tag}_{unique_id}",
                    email=f"{tag}_{unique_id}@example.com",
                    password="TestPass123!",
                )
            )
        group = messaging_manager.create_group(
            users[0].id, f"L6 Group {unique_id}", [users[1].id, users[2].id]
        )
        msg = messaging_manager.send_message(users[0].id, group.id, "deterministic")
        for u in users:
            reaction_manager.add_reaction(u.id, msg.id, "👍")

        # Both other users are blocked (asymmetric relationships land in
        # rel_blocked, so the set has >1 member regardless of direction).
        rel_manager.block_user(users[0].id, users[1].id)
        rel_manager.block_user(users[0].id, users[2].id)

        captured = []
        orig_fetch_all = test_db.fetch_all

        def spy_fetch_all(query, params=None):
            if "NOT IN" in (query or ""):
                captured.append(list(params) if params else [])
            return orig_fetch_all(query, params)

        test_db.fetch_all = spy_fetch_all
        try:
            reaction_manager.get_reactions(users[0].id, msg.id)
            reaction_manager.get_reactions(users[0].id, msg.id)
        finally:
            test_db.fetch_all = orig_fetch_all

        assert len(captured) >= 2, "blocked-user query must be captured"
        first, second = captured[0], captured[1]
        assert first == second, "parameter order must be stable across calls"
        # The params are [viewer_id, message_id, *blocked_ids]; the blocked
        # suffix must be ascending (deterministic across runs).
        blocked_suffix = first[2:]
        assert blocked_suffix == sorted(blocked_suffix)


class TestParticipantMetadataGuard:
    """L7: get_participant_ids must tolerate NULL/missing metadata."""

    def test_null_metadata_does_not_crash(
        self, auth_manager, messaging_manager, reaction_manager
    ):
        unique_id = uuid.uuid4().hex[:8]
        user1 = auth_manager.register(
            username=f"l7a_{unique_id}",
            email=f"l7a_{unique_id}@example.com",
            password="TestPass123!",
        )
        user2 = auth_manager.register(
            username=f"l7b_{unique_id}",
            email=f"l7b_{unique_id}@example.com",
            password="TestPass123!",
        )
        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Force the metadata column to NULL on the DM conversation.
        messaging_manager._db.execute(
            "UPDATE msg_conversations SET metadata = NULL WHERE id = ?", (dm.id,)
        )

        participant_ids = reaction_manager.get_participant_ids(dm.id)
        assert user1.id in participant_ids
        assert user2.id in participant_ids
