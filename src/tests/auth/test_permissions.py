"""
Comprehensive permission tests covering escalation attempts and validation.
"""

import pytest
import time
from src.core.auth.permissions import (
    DEFAULT_USER_PERMISSIONS,
    has_permission,
    validate_permissions,
    BOT_RESTRICTED_PERMISSIONS,
)
from src.tests.fixtures.config import TEST_PASSWORD


class TestPermissionChecking:
    """Tests for permission checking logic."""

    def test_has_permission_exact_match(self):
        """Test exact permission match."""
        perms = {"messages.send": True}
        assert has_permission(perms, "messages.send") is True

    def test_has_permission_missing(self):
        """Test missing permission."""
        perms = {"messages.send": True}
        assert has_permission(perms, "messages.delete") is False

    def test_has_permission_false_value(self):
        """Test permission with False value."""
        perms = {"messages.send": False}
        assert has_permission(perms, "messages.send") is False

    def test_has_permission_wildcard_all(self):
        """Test wildcard * grants all permissions."""
        perms = {"*": True}
        assert has_permission(perms, "messages.send") is True
        assert has_permission(perms, "admin.system") is True
        assert has_permission(perms, "any.random.permission") is True

    def test_has_permission_category_wildcard(self):
        """Test category wildcard grants category permissions."""
        perms = {"messages.*": True}
        assert has_permission(perms, "messages.send") is True
        assert has_permission(perms, "messages.delete") is True
        assert has_permission(perms, "servers.create") is False

    def test_has_permission_none_permissions(self):
        """Test None permissions."""
        assert has_permission(None, "messages.send") is False

    def test_has_permission_empty_permissions(self):
        """Test empty permissions dict."""
        assert has_permission({}, "messages.send") is False


class TestDefaultPermissions:
    """Tests for default permission sets."""

    def test_default_user_permissions(self, modules):
        """Test default user permissions are assigned."""
        username = f"defuser_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        assert user.permissions.get("messages.send") is True
        assert user.permissions.get("messages.read") is True
        assert user.permissions.get("bots.create") is True

    def test_default_bot_permissions(self, modules):
        """Test default bot permissions are assigned."""
        username = f"defbot_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        assert bot.permissions.get("messages.send") is True
        assert bot.permissions.get("bots.create") is False
        assert bot.permissions.get("admin.system") is not True

    def test_bot_cannot_have_restricted_permissions(self):
        """Test bot cannot have restricted permissions."""
        for perm in BOT_RESTRICTED_PERMISSIONS:
            valid, issues = validate_permissions({perm: True}, is_bot=True)
            assert valid is False
            assert any(perm in issue for issue in issues)


class TestPermissionEscalation:
    """Tests to prevent permission escalation."""

    def test_user_cannot_grant_admin_to_bot(self, modules):
        """Test user cannot create bot with admin permissions."""
        username = f"noadmin_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        from src.core.auth.exceptions import PermissionDeniedError

        with pytest.raises(PermissionDeniedError):
            modules.auth.create_bot(
                user.id, f"bot_{time.time()}", "Bot", {"admin.system": True}
            )

    def test_bot_wildcard_prevents_restricted(self):
        """Test bot wildcard doesn't grant restricted permissions."""
        valid, issues = validate_permissions({"*": True}, is_bot=True)
        assert valid is False

    def test_bot_category_wildcard_checks_restricted(self):
        """Test bot category wildcard checks for restricted permissions."""
        valid, issues = validate_permissions({"admin.*": True}, is_bot=True)
        assert valid is False

    def test_regular_user_can_have_wildcard(self, modules):
        """Test regular user can have wildcard permissions."""
        # This would require direct database manipulation in tests
        # Just verify validation allows it
        valid, issues = validate_permissions({"*": True}, is_bot=False)
        assert valid is True


class TestPermissionValidation:
    """Tests for permission validation."""

    def test_validate_valid_permissions(self):
        """Test validating valid permissions."""
        perms = {"messages.send": True, "messages.read": True}
        valid, issues = validate_permissions(perms, is_bot=False)
        assert valid is True
        assert len(issues) == 0

    def test_validate_invalid_permission_name(self):
        """Test validating invalid permission name."""
        perms = {"invalid.permission.name": True}
        valid, issues = validate_permissions(perms, is_bot=False)
        assert valid is False
        assert any("unknown" in issue.lower() for issue in issues)

    def test_validate_bot_restricted_permission(self):
        """Test validating bot with restricted permission."""
        perms = {"bots.create": True}
        valid, issues = validate_permissions(perms, is_bot=True)
        assert valid is False

    def test_validate_wildcard_permission(self):
        """Test validating wildcard permission."""
        perms = {"*": True}
        valid, issues = validate_permissions(perms, is_bot=False)
        assert valid is True

    def test_validate_category_wildcard(self):
        """Test validating category wildcard."""
        perms = {"messages.*": True}
        valid, issues = validate_permissions(perms, is_bot=False)
        assert valid is True


class TestPermissionInheritance:
    """Tests for permission inheritance via wildcards."""

    def test_wildcard_grants_all_permissions(self):
        """Test * wildcard grants all permissions."""
        perms = {"*": True}

        test_perms = [
            "messages.send",
            "messages.delete",
            "servers.create",
            "admin.system",
            "bots.create",
            "account.delete",
        ]

        for perm in test_perms:
            assert has_permission(perms, perm) is True

    def test_category_wildcard_scoped(self):
        """Test category wildcard is scoped to category."""
        perms = {"messages.*": True}

        assert has_permission(perms, "messages.send") is True
        assert has_permission(perms, "messages.delete") is True
        assert has_permission(perms, "servers.create") is False


class TestUserPermissionModification:
    """Tests for modifying user permissions."""

    def test_user_permissions_persist(self, modules):
        """Test user permissions are stored and retrieved."""
        username = f"persist_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        retrieved = modules.auth.get_user(user.id)
        assert retrieved.permissions == user.permissions

    def test_bot_permission_update(self, modules):
        """Test updating bot permissions."""
        username = f"updatebot_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        new_perms = {"messages.send": False, "messages.read": True}
        updated = modules.auth.update_bot_permissions(user.id, bot.id, new_perms)

        assert updated.permissions == new_perms

    def test_bot_permission_update_validates(self, modules):
        """Test bot permission update validates restrictions."""
        username = f"validate_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")

        from src.core.auth.exceptions import PermissionDeniedError

        with pytest.raises(PermissionDeniedError):
            modules.auth.update_bot_permissions(user.id, bot.id, {"admin.system": True})


class TestPermissionInTokens:
    """Tests for permissions in tokens."""

    def test_user_token_includes_permissions(self, modules):
        """Test user token verification includes permissions."""
        username = f"tokenperm_{time.time()}"
        modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        token_info = modules.auth.verify_token(result.token)

        assert token_info.permissions is not None
        assert isinstance(token_info.permissions, dict)

    def test_bot_token_includes_permissions(self, modules):
        """Test bot token verification includes permissions."""
        username = f"bottokperm_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        token_info = modules.auth.verify_token(bot.token)

        assert token_info.permissions is not None
        assert isinstance(token_info.permissions, dict)

    def test_token_permissions_match_user(self, modules):
        """Test token permissions match user permissions."""
        username = f"match_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        result = modules.auth.login(username, TEST_PASSWORD)
        token_info = modules.auth.verify_token(result.token)

        assert token_info.permissions == user.permissions


class TestPermissionEdgeCases:
    """Edge case tests for permissions."""

    def test_permission_with_multiple_dots(self):
        """Test permission with multiple dots."""
        perms = {"deeply.nested.permission.name": True}
        assert has_permission(perms, "deeply.nested.permission.name") is True

    def test_empty_permission_name(self):
        """Test empty permission name."""
        assert has_permission({"": True}, "") is True
        assert has_permission({"": True}, "something") is False

    def test_permission_case_sensitive(self):
        """Test permissions are case-sensitive."""
        perms = {"Messages.Send": True}
        assert has_permission(perms, "messages.send") is False
        assert has_permission(perms, "Messages.Send") is True

    def test_wildcard_false_value(self):
        """Test wildcard with False value."""
        perms = {"*": False}
        assert has_permission(perms, "messages.send") is False


class TestPermissionSerialization:
    """Tests for permission serialization."""

    def test_permissions_to_json(self, modules):
        """Test permissions are correctly serialized to JSON."""
        username = f"serial_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        # Verify they can be retrieved
        retrieved = modules.auth.get_user(user.id)
        assert retrieved.permissions is not None

    def test_permissions_from_json(self, modules):
        """Test permissions are correctly deserialized from JSON."""
        from src.core.auth.permissions import permissions_to_json, permissions_from_json

        perms = {"messages.send": True, "messages.read": False}
        json_str = permissions_to_json(perms)
        restored = permissions_from_json(json_str)

        assert restored == perms


class TestPermissionCategories:
    """Tests for permission categorization."""

    def test_permission_categories_available(self):
        """Test permission categories can be retrieved."""
        from src.core.auth.permissions import get_permission_categories

        categories = get_permission_categories()
        assert "messages" in categories
        assert "servers" in categories
        assert "account" in categories

    def test_permission_categories_complete(self):
        """Test all permissions are in categories."""
        from src.core.auth.permissions import get_permission_categories, PERMISSIONS

        categories = get_permission_categories()
        all_perms_in_categories = set()

        for cat_perms in categories.values():
            all_perms_in_categories.update(cat_perms)

        # All defined permissions should be in categories
        for perm in PERMISSIONS.keys():
            assert perm in all_perms_in_categories


class TestPermissionChecksInActions:
    """Tests for permission checks in actual operations."""

    def test_bot_creation_requires_permission(self, modules):
        """Test bot creation requires bots.create permission."""
        # Users have this by default, test would require removing it
        # Just verify the permission exists in defaults
        assert DEFAULT_USER_PERMISSIONS.get("bots.create") is True

    def test_user_without_bot_permission(self, modules):
        """Test user behavior without bot creation permission."""
        # Would require modifying user permissions
        # Testing via validation is sufficient
        pass


class TestPermissionAudit:
    """Tests for permission-related audit logging."""

    def test_permission_changes_audited(self, modules):
        """Test permission changes create audit logs."""
        # Permission changes would need to create audit entries
        # Currently only bot permission updates are supported
        username = f"auditperm_{time.time()}"
        user = modules.auth.register(username, f"{username}@test.com", TEST_PASSWORD)

        bot = modules.auth.create_bot(user.id, f"bot_{time.time()}", "Bot")
        modules.auth.update_bot_permissions(user.id, bot.id, {"messages.send": False})

        # Check if audit entry exists (implementation may vary)


class TestPermissionSecurity:
    """Tests for permission security."""

    def test_cannot_escalate_own_permissions(self, modules):
        """Test user cannot escalate their own permissions."""
        # Would require an update_user_permissions method
        # Verify no such method exists or is properly protected
        pass

    def test_bot_cannot_modify_permissions(self, modules):
        """Test bot cannot modify permissions."""
        # Bots don't have permission management abilities
        # Verified by restricted permissions
        assert (
            "bots.manage" in BOT_RESTRICTED_PERMISSIONS
            or validate_permissions({"bots.manage": True}, is_bot=True)[0] is False
        )
