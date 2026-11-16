"""Reaction emoji and bulk-array boundaries."""

import pytest

from src.core.reactions.manager.bounds import (
    MAX_BULK_MESSAGE_IDS,
    MAX_REACTION_EMOJI_LENGTH,
    validate_bulk_message_ids,
    validate_route_emoji,
)


class TestRouteEmoji:
    @pytest.mark.parametrize(
        "value",
        [
            "%F0%9F%91%8D",  # URL-encoded thumbs up
            ":thumbsup:",
            "<:custom:12345>",
            "1\ufe0f\u20e3",  # keycap
        ],
    )
    def test_valid_emoji(self, value):
        assert validate_route_emoji(value)

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "a" * (MAX_REACTION_EMOJI_LENGTH + 1),
            "hi\x00there",
            "x\u202ey",  # bidi override
            "x\u200by",  # zero-width space
            "x\ud800y",  # unpaired surrogate
        ],
    )
    def test_invalid_emoji_rejected(self, value):
        with pytest.raises(ValueError):
            validate_route_emoji(value)

    def test_zwj_sequences_allowed(self):
        assert validate_route_emoji("\U0001f468\u200d\U0001f469\u200d\U0001f467")


class TestBulkMessageIds:
    def test_valid_ids(self):
        assert validate_bulk_message_ids([1, "2", 3]) == [1, 2, 3]

    def test_duplicates_removed(self):
        assert validate_bulk_message_ids([1, 2, 1]) == [1, 2]

    def test_empty(self):
        assert validate_bulk_message_ids([]) == []

    def test_oversized_array_rejected(self):
        with pytest.raises(ValueError, match="exceeds maximum"):
            validate_bulk_message_ids(list(range(1, MAX_BULK_MESSAGE_IDS + 2)))

    @pytest.mark.parametrize("value", [0, -1, 10**30, "abc", None, 1.5])
    def test_invalid_id_rejected(self, value):
        with pytest.raises(ValueError):
            validate_bulk_message_ids([value])

    def test_non_sequence_rejected(self):
        with pytest.raises(ValueError):
            validate_bulk_message_ids("123")


class TestRouteWiring:
    def test_all_emoji_routes_share_validator(self):
        import inspect

        import src.api.routes.reactions as routes

        source = inspect.getsource(routes)
        assert source.count("_validated_emoji(") >= 5
        assert "validate_route_emoji" in source

    def test_bulk_manager_methods_are_bounded(self):
        from src.core.reactions.manager.reactions import ReactionOpsMixin

        for name in ("get_reactions_batch", "user_reacted_to_messages"):
            source = __import__("inspect").getsource(getattr(ReactionOpsMixin, name))
            assert "validate_bulk_message_ids" in source
