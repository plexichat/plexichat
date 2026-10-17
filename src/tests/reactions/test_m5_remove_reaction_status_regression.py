"""
M5 regression: remove_reaction maps InvalidEmojiError to 400, not 500.

Route-level tests calling src.api.routes.reactions.remove_reaction directly
with a mocked reactions manager (established pattern in
src/tests/reactions/test_reaction_regressions.py).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.core.reactions.exceptions import InvalidEmojiError


def _ctx(monkeypatch_ctx=None):
    request = SimpleNamespace(state=SimpleNamespace(is_internal=False))
    current_user = SimpleNamespace(user_id=1)
    return request, current_user


class TestM5RemoveReactionStatus:
    """M5: DELETE/remove with invalid emoji must be 400, not 500."""

    @pytest.mark.asyncio
    async def test_remove_invalid_emoji_returns_400(self):
        from src.api.routes.reactions import remove_reaction

        fake = MagicMock()
        fake.remove_reaction.side_effect = InvalidEmojiError("bad emoji")
        request, current_user = _ctx()
        with (
            patch("src.api.get_reactions", return_value=fake),
            patch(
                "src.api.routes.reactions.ratelimit.acheck_rate_limit",
                new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
            ),
            patch(
                "src.api.routes.reactions._validate_message_in_channel",
                return_value=5,
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await remove_reaction("5", "5", "not-an-emoji", request, current_user)

        assert exc.value.status_code == 400
        assert exc.value.status_code != 500

    @pytest.mark.asyncio
    async def test_remove_genuine_server_error_still_500(self):
        from src.api.routes.reactions import remove_reaction

        fake = MagicMock()
        fake.remove_reaction.side_effect = RuntimeError("db exploded")
        request, current_user = _ctx()
        with (
            patch("src.api.get_reactions", return_value=fake),
            patch(
                "src.api.routes.reactions.ratelimit.acheck_rate_limit",
                new=AsyncMock(return_value=SimpleNamespace(allowed=True)),
            ),
            patch(
                "src.api.routes.reactions._validate_message_in_channel",
                return_value=5,
            ),
        ):
            with pytest.raises(HTTPException) as exc:
                await remove_reaction("5", "5", "👍", request, current_user)

        assert exc.value.status_code == 500
