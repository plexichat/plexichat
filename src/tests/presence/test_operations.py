"""Comprehensive Presence tests targeting 80%+ coverage."""

import pytest

from src.core.presence import UserNotFoundError
from src.core.presence.models import UserStatus


class TestPresenceErrors:
    def test_invalid_user(self, presence_manager, monkeypatch):
        """Non-existent user validation."""
        monkeypatch.setattr(presence_manager, "_user_exists", lambda x: False)

        with pytest.raises(UserNotFoundError):
            presence_manager.set_status(999999, UserStatus.ONLINE)
