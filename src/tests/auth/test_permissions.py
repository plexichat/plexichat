"""
Permission system tests for auth module.
"""

import pytest
from src.core.auth.permissions import (
    has_permission, validate_permissions, merge_permissions,
    PERMISSIONS, DEFAULT_USER_PERMISSIONS, DEFAULT_BOT_PERMISSIONS,
    BOT_RESTRICTED_PERMISSIONS
)


class TestPermissions:
    """Test permission system."""
    
    def test_has_exact_permission(self):
        """Test checking exact permission."""
        perms = {"messages.send": True}
        
        assert has_permission(perms, "messages.send") is True
        assert has_permission(perms, "messages.read") is False
    
    def test_has_wildcard_permission(self):
        """Test checking wildcard permission."""
        perms = {"messages.*": True}
        
        assert has_permission(perms, "messages.send") is True
        assert has_permission(perms, "messages.read") is True
        assert has_permission(perms, "messages.delete") is True
        assert has_permission(perms, "conversations.create") is False
    
    def test_has_full_wildcard(self):
        """Test full wildcard grants all permissions."""
        perms = {"*": True}
        
        assert has_permission(perms, "messages.send") is True
        assert has_permission(perms, "admin.system") is True
        assert has_permission(perms, "anything.at.all") is True
    
    def test_missing_permission(self):
        """Test missing permission returns False."""
        perms = {"messages.send": True}
        
        assert has_permission(perms, "messages.delete") is False
    
    def test_empty_permissions(self):
        """Test empty permissions dict."""
        perms = {}
        
        assert has_permission(perms, "messages.send") is False
    
    def test_none_permissions(self):
        """Test None permissions."""
        assert has_permission(None, "messages.send") is False
    
    def test_permission_set_to_false(self):
        """Test explicitly False permission."""
        perms = {"messages.send": False}
        
        assert has_permission(perms, "messages.send") is False
    
    def test_validate_valid_permissions(self):
        """Test validating valid permissions."""
        perms = {"messages.send": True, "messages.read": True}
        
        valid, issues = validate_permissions(perms)
        
        assert valid is True
        assert len(issues) == 0
    
    def test_validate_unknown_permission(self):
        """Test validating unknown permission."""
        perms = {"unknown.permission": True}
        
        valid, issues = validate_permissions(perms)
        
        assert valid is False
        assert any("Unknown" in issue for issue in issues)
    
    def test_validate_bot_restricted_permission(self):
        """Test validating bot with restricted permission."""
        perms = {"bots.create": True}
        
        valid, issues = validate_permissions(perms, is_bot=True)
        
        assert valid is False
        assert any("cannot have" in issue.lower() for issue in issues)
    
    def test_validate_bot_wildcard_restricted(self):
        """Test validating bot with wildcard that includes restricted."""
        perms = {"admin.*": True}
        
        valid, issues = validate_permissions(perms, is_bot=True)
        
        assert valid is False
    
    def test_merge_permissions(self):
        """Test merging permissions."""
        base = {"messages.send": True, "messages.read": True}
        override = {"messages.read": False, "messages.delete": True}
        
        result = merge_permissions(base, override)
        
        assert result["messages.send"] is True
        assert result["messages.read"] is False
        assert result["messages.delete"] is True
    
    def test_default_user_permissions_valid(self):
        """Test default user permissions are valid."""
        valid, issues = validate_permissions(DEFAULT_USER_PERMISSIONS)
        
        assert valid is True
    
    def test_default_bot_permissions_valid(self):
        """Test default bot permissions are valid for bots."""
        valid, issues = validate_permissions(DEFAULT_BOT_PERMISSIONS, is_bot=True)
        
        assert valid is True
    
    def test_bot_restricted_not_in_defaults(self):
        """Test restricted permissions not in bot defaults."""
        for restricted in BOT_RESTRICTED_PERMISSIONS:
            assert restricted not in DEFAULT_BOT_PERMISSIONS or \
                   DEFAULT_BOT_PERMISSIONS.get(restricted) is not True
    
    def test_has_capability_integration(self, logged_in_user):
        """Test has_capability with real token."""
        user, token, auth, username = logged_in_user
        
        token_info = auth.verify_token(token)
        
        assert auth.has_capability(token_info, "messages.send") is True
        assert auth.has_capability(token_info, "admin.system") is False
    
    def test_require_capability_passes(self, logged_in_user):
        """Test require_capability passes for valid permission."""
        user, token, auth, username = logged_in_user
        
        token_info = auth.verify_token(token)
        
        # Should not raise
        auth.require_capability(token_info, "messages.send")
    
    def test_require_capability_raises(self, logged_in_user):
        """Test require_capability raises for missing permission."""
        user, token, auth, username = logged_in_user
        
        token_info = auth.verify_token(token)
        
        with pytest.raises(auth.PermissionDeniedError):
            auth.require_capability(token_info, "admin.system")
