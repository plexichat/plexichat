"""Comprehensive Presence tests targeting 80%+ coverage."""


class TestPresenceErrors:
    def test_invalid_user(self, presence_manager, monkeypatch):
        """Non-existent user validation."""
        monkeypatch.setattr(presence_manager, "_user_exists", lambda x: False)
