"""
Audit logging tests for auth module.
"""

import pytest


class TestAudit:
    """Test audit logging."""
    
    def test_login_creates_audit_entry(self, registered_user):
        """Test successful login creates audit entry."""
        user, auth, username = registered_user
        
        auth.login(username, "TestPass123!")
        
        history = auth.get_login_history(user.id)
        assert any(e.event_type == auth.AuditEventType.LOGIN_SUCCESS for e in history)
    
    def test_failed_login_creates_audit_entry(self, registered_user):
        """Test failed login creates audit entry."""
        user, auth, username = registered_user
        
        try:
            auth.login(username, "WrongPassword!")
        except auth.InvalidCredentialsError:
            pass
        
        history = auth.get_login_history(user.id)
        assert any(e.event_type == auth.AuditEventType.LOGIN_FAILED for e in history)
    
    def test_logout_creates_audit_entry(self, logged_in_user):
        """Test logout creates audit entry."""
        user, token, auth, username = logged_in_user
        
        # Create new session to logout
        result = auth.login(username, "TestPass123!")
        auth.logout(result.token)
        
        history = auth.get_login_history(user.id)
        assert any(e.event_type == auth.AuditEventType.LOGOUT for e in history)
    
    def test_password_change_creates_audit_entry(self, db_and_auth):
        """Test password change creates audit entry."""
        db, auth = db_and_auth
        
        user = auth.register("auditpwd", "auditpwd@example.com", "TestPass123!")
        auth.change_password(user.id, "TestPass123!", "NewSecurePass456!")
        
        events = auth.get_security_events(user.id)
        assert any(e.event_type == auth.AuditEventType.PASSWORD_CHANGE for e in events)
    
    def test_2fa_enable_creates_audit_entry(self, db_and_auth):
        """Test enabling 2FA creates audit entry."""
        db, auth = db_and_auth
        import pyotp
        
        user = auth.register("audit2fa", "audit2fa@example.com", "TestPass123!")
        setup = auth.setup_2fa(user.id)
        totp = pyotp.TOTP(setup.secret)
        auth.confirm_2fa(user.id, totp.now())
        
        events = auth.get_security_events(user.id)
        assert any(e.event_type == auth.AuditEventType.TWO_FACTOR_ENABLED for e in events)
    
    def test_session_revoke_creates_audit_entry(self, registered_user):
        """Test session revocation creates audit entry."""
        user, auth, username = registered_user
        
        result = auth.login(username, "TestPass123!")
        session_id = int(result.token.split(".")[0])
        
        auth.revoke_session(user.id, session_id)
        
        events = auth.get_security_events(user.id)
        assert any(e.event_type == auth.AuditEventType.SESSION_REVOKED for e in events)
    
    def test_audit_entry_contains_ip(self, registered_user):
        """Test audit entry contains IP address."""
        user, auth, username = registered_user
        
        auth.login(username, "TestPass123!", ip_address="192.168.1.100")
        
        history = auth.get_login_history(user.id)
        entry = next(e for e in history if e.event_type == auth.AuditEventType.LOGIN_SUCCESS and e.ip_address == "192.168.1.100")
        
        assert entry.ip_address == "192.168.1.100"
    
    def test_get_login_history_ordered(self, registered_user):
        """Test login history is ordered by timestamp descending."""
        user, auth, username = registered_user
        import time
        
        auth.login(username, "TestPass123!")
        time.sleep(0.05)
        auth.login(username, "TestPass123!")
        
        history = auth.get_login_history(user.id)
        
        for i in range(len(history) - 1):
            assert history[i].timestamp >= history[i + 1].timestamp
    
    def test_get_security_events_limit(self, registered_user):
        """Test security events respects limit."""
        user, auth, username = registered_user
        
        for _ in range(10):
            auth.login(username, "TestPass123!")
        
        events = auth.get_security_events(user.id, limit=5)
        assert len(events) <= 5
    
    def test_audit_success_flag(self, registered_user):
        """Test audit entry has correct success flag."""
        user, auth, username = registered_user
        
        auth.login(username, "TestPass123!")
        
        try:
            auth.login(username, "WrongPassword!")
        except auth.InvalidCredentialsError:
            pass
        
        history = auth.get_login_history(user.id)
        
        success_entries = [e for e in history if e.event_type == auth.AuditEventType.LOGIN_SUCCESS]
        failed_entries = [e for e in history if e.event_type == auth.AuditEventType.LOGIN_FAILED]
        
        assert any(e.success is True for e in success_entries)
        assert any(e.success is False for e in failed_entries)
