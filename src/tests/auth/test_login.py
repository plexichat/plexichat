"""
Login tests for auth module.
"""

import pytest
import time


class TestLogin:
    """Test user login."""
    
    def test_login_success(self, registered_user):
        """Test successful login."""
        user, auth, username = registered_user
        
        result = auth.login(username, "TestPass123!")
        
        assert result.status == auth.AuthStatus.SUCCESS
        assert result.token is not None
        assert result.user is not None
        assert result.user.username == username
    
    def test_login_with_email(self, registered_user):
        """Test login with email instead of username."""
        user, auth, username = registered_user
        
        result = auth.login(user.email, "TestPass123!")
        
        assert result.status == auth.AuthStatus.SUCCESS
    
    def test_login_wrong_password(self, registered_user):
        """Test login with wrong password."""
        user, auth, username = registered_user
        
        with pytest.raises(auth.InvalidCredentialsError):
            auth.login(username, "WrongPassword123!")
    
    def test_login_nonexistent_user(self, db_and_auth):
        """Test login with nonexistent user."""
        db, auth = db_and_auth
        
        with pytest.raises(auth.InvalidCredentialsError):
            auth.login("nobody_exists_12345", "Password123!")
    
    def test_login_increments_failed_attempts(self, registered_user):
        """Test that failed login increments counter."""
        user, auth, username = registered_user
        
        initial = auth.get_user(user.id).failed_login_attempts
        
        try:
            auth.login(username, "WrongPassword!")
        except auth.InvalidCredentialsError:
            pass
        
        updated_user = auth.get_user(user.id)
        assert updated_user.failed_login_attempts == initial + 1
    
    def test_login_locks_after_max_attempts(self, db_and_auth):
        """Test account locks after max failed attempts."""
        db, auth = db_and_auth
        
        # Create fresh user for this test
        user = auth.register("locktest", "locktest@example.com", "TestPass123!")
        
        # Config has max_failed_attempts = 3
        for _ in range(3):
            try:
                auth.login("locktest", "WrongPassword!")
            except auth.InvalidCredentialsError:
                pass
        
        with pytest.raises(auth.AccountLockedError):
            auth.login("locktest", "TestPass123!")
    
    def test_login_creates_session(self, registered_user):
        """Test that login creates a session."""
        user, auth, username = registered_user
        
        result = auth.login(username, "TestPass123!")
        
        sessions = auth.get_sessions(user.id)
        assert len(sessions) >= 1
    
    def test_login_with_device_info(self, registered_user):
        """Test login with device info creates device record."""
        user, auth, username = registered_user
        
        fp = f"device_{user.id}"
        result = auth.login(
            username,
            "TestPass123!",
            device_info={"fingerprint": fp, "name": "Test Device", "type": "desktop"}
        )
        
        assert result.status == auth.AuthStatus.SUCCESS
        
        devices = auth.get_devices(user.id)
        assert any(d.fingerprint == fp for d in devices)
    
    def test_login_updates_last_login(self, registered_user):
        """Test that login updates last_login_at."""
        user, auth, username = registered_user
        
        auth.login(username, "TestPass123!")
        
        after = auth.get_user(user.id).last_login_at
        assert after is not None
    
    def test_login_resets_failed_attempts(self, db_and_auth):
        """Test successful login resets failed attempts."""
        db, auth = db_and_auth
        
        user = auth.register("resettest", "resettest@example.com", "TestPass123!")
        
        # Fail once
        try:
            auth.login("resettest", "WrongPassword!")
        except auth.InvalidCredentialsError:
            pass
        
        assert auth.get_user(user.id).failed_login_attempts >= 1
        
        # Succeed
        auth.login("resettest", "TestPass123!")
        
        assert auth.get_user(user.id).failed_login_attempts == 0
    
    def test_login_creates_audit_entry(self, registered_user):
        """Test that login creates audit log entry."""
        user, auth, username = registered_user
        
        auth.login(username, "TestPass123!")
        
        history = auth.get_login_history(user.id)
        assert len(history) >= 1
        assert any(e.event_type == auth.AuditEventType.LOGIN_SUCCESS for e in history)
    
    def test_login_failed_creates_audit_entry(self, registered_user):
        """Test that failed login creates audit entry."""
        user, auth, username = registered_user
        
        try:
            auth.login(username, "WrongPassword!")
        except auth.InvalidCredentialsError:
            pass
        
        history = auth.get_login_history(user.id)
        assert any(e.event_type == auth.AuditEventType.LOGIN_FAILED for e in history)
    
    def test_multiple_logins_create_multiple_sessions(self, registered_user):
        """Test multiple logins create separate sessions."""
        user, auth, username = registered_user
        
        result1 = auth.login(username, "TestPass123!")
        result2 = auth.login(username, "TestPass123!")
        
        assert result1.token != result2.token
