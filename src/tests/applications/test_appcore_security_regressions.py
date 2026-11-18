"""Regression tests for applications-core hardening gaps.

Covers:
  * Permission vocabulary bypass: ``update_command`` historically skipped the
    ``default_member_permissions`` check that ``register_command`` enforces.
  * Unbounded bot data: bot profile tags (and other free-form bot metadata)
    had no core-level ceiling, so a single owner could inflate the public
    directory without bound.
"""

from unittest.mock import MagicMock

import pytest

from src.core.security.hardening import HardeningViolation


# ---------------------------------------------------------------------------
# Permission vocabulary bypass on command update
# ---------------------------------------------------------------------------
class TestCommandUpdatePermissionValidation:
    def test_update_validator_rejects_non_decimal_permission_string(self):
        from src.core.applications.commands.validation import validate_command_update

        for bad in ("*", "-1", "admin", "8;drop", "9" * 100):
            valid, issues = validate_command_update({"default_member_permissions": bad})
            assert valid is False
            assert any("permission" in issue.lower() for issue in issues)

    def test_update_validator_accepts_valid_permission_string(self):
        from src.core.applications.commands.validation import validate_command_update

        valid, issues = validate_command_update({"default_member_permissions": "8"})
        assert valid is True, issues

    def test_update_command_does_not_write_invalid_permission_string(self):
        from src.core.applications.commands.registry import CommandRegistry
        from src.core.applications.exceptions import CommandValidationError

        db = MagicMock()
        db.fetch_one.return_value = {
            "id": 1,
            "application_id": 10,
            "name": "test",
            "description": "d",
            "command_type": 1,
            "server_id": None,
            "options": "[]",
            "default_member_permissions": "0",
            "dm_permission": 1,
            "nsfw": 0,
            "version": 1,
            "created_at": 0,
            "updated_at": 0,
        }
        registry = CommandRegistry(db, {})

        with pytest.raises(CommandValidationError):
            registry.update_command(1, default_member_permissions="*")

        db.execute.assert_not_called()

    def test_update_command_still_accepts_valid_permission_string(self):
        from src.core.applications.commands.registry import CommandRegistry

        db = MagicMock()
        db.fetch_one.return_value = {
            "id": 1,
            "application_id": 10,
            "name": "test",
            "description": "d",
            "command_type": 1,
            "server_id": None,
            "options": "[]",
            "default_member_permissions": "0",
            "dm_permission": 1,
            "nsfw": 0,
            "version": 1,
            "created_at": 0,
            "updated_at": 0,
        }
        registry = CommandRegistry(db, {})

        registry.update_command(1, default_member_permissions="8")
        assert db.execute.called


# ---------------------------------------------------------------------------
# Unbounded bot data
# ---------------------------------------------------------------------------
class TestBotDataBounds:
    def test_validate_bot_tags_bounds_count_and_length(self):
        from src.core.applications.validation import validate_bot_tags

        assert validate_bot_tags(["chat", "moderation"]) == ["chat", "moderation"]
        # Duplicates are dropped, keeping the list canonical.
        assert validate_bot_tags(["chat", "chat"]) == ["chat"]
        with pytest.raises(HardeningViolation):
            validate_bot_tags(["x"] * 100)
        with pytest.raises(HardeningViolation):
            validate_bot_tags(["x" * 100])

    def test_update_bot_profile_rejects_unbounded_tags_before_db_write(self):
        from src.core.applications.manager.bot_management import BotManagementMixin
        from src.core.applications.models import Application

        class BareBotManager(BotManagementMixin):
            def __init__(self, db):
                self._db = db
                self._config = {}
                self._auth = None
                self._oauth = None
                self._commands = None
                self._interactions = None
                self._servers = None

            def _get_timestamp(self):
                return 1000

        db = MagicMock()
        manager = BareBotManager(db)
        manager.get_application = MagicMock(
            return_value=Application(
                id=1,
                owner_id=2,
                name="Bot App",
                description=None,
                icon_url=None,
                bot_id=None,
                bot_public=True,
                bot_require_code_grant=False,
                terms_of_service_url=None,
                privacy_policy_url=None,
                redirect_uris=[],
                interactions_endpoint_url=None,
                created_at=0,
                updated_at=0,
            )
        )

        with pytest.raises(HardeningViolation):
            manager.update_bot_profile(application_id=1, user_id=2, tags=["x"] * 100)
        db.execute.assert_not_called()

    def test_approve_bot_rejects_unbounded_bot_name(self):
        from src.core.applications.manager.bot_management import BotManagementMixin

        class BareBotManager(BotManagementMixin):
            def __init__(self, db):
                self._db = db
                self._config = {}
                self._auth = None
                self._oauth = None
                self._commands = None
                self._interactions = None
                self._servers = MagicMock()
                self._servers.has_permission.return_value = True

            def _get_timestamp(self):
                return 1000

            def _generate_id(self):
                return 1

            def _check_bot_license(self):
                return True

            def _check_premium_license(self):
                return False

        db = MagicMock()
        manager = BareBotManager(db)
        with pytest.raises(HardeningViolation):
            manager.approve_bot(100, 200, 300, bot_name="x" * 5000)
        db.execute.assert_not_called()
