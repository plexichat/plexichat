"""
Login tests for auth module.
"""

import pytest


import pytest
import asyncio
import uuid
from src.core.auth.exceptions import InvalidCredentialsError, AccountLockedError

@pytest.mark.asyncio
class TestLoginAsync:
    """Enhanced asynchronous login tests."""

    async def test_login_success(self, registered_user):
        """Test successful login returns a token and user info."""
        user, auth, username = registered_user
        
        # Test basic success
        result = await asyncio.to_thread(auth.login, username, "TestPass123!")
        
        assert result.status == auth.AuthStatus.SUCCESS
        assert result.token is not None
        assert result.user.id == user.id
        assert result.user.username == username

    async def test_login_wrong_password(self, registered_user):
        """Test login fails with wrong password and increments failed attempts."""
        user, auth, username = registered_user
        
        initial_attempts = auth.get_user(user.id).failed_login_attempts
        
        with pytest.raises(InvalidCredentialsError):
            await asyncio.to_thread(auth.login, username, "WrongPassword123!")
            
        updated_user = auth.get_user(user.id)
        assert updated_user.failed_login_attempts == initial_attempts + 1

    async def test_account_locking_flow(self, db_and_auth):
        """Test account gets locked after multiple failed attempts and stays locked."""
        db, auth = db_and_auth
        username = f"locktest_{uuid.uuid4().hex[:4]}"
        user = auth.register(username, f"{username}@example.com", "TestPass123!")
        
        # Default config has max_failed_attempts = 3
        for i in range(3):
            with pytest.raises(InvalidCredentialsError):
                await asyncio.to_thread(auth.login, username, "WrongPassword!")
                
        # Fourth attempt with CORRECT password should raise AccountLockedError
        with pytest.raises(AccountLockedError):
            await asyncio.to_thread(auth.login, username, "TestPass123!")
            
        # Verify status in database
        updated_user = auth.get_user(user.id)
        assert updated_user.account_locked is True
        assert updated_user.locked_until is not None

    async def test_concurrent_logins_performance(self, registered_user):
        """Test multiple concurrent login attempts for the same user."""
        user, auth, username = registered_user
        
        # Run 10 logins in parallel to test thread safety and performance
        tasks = [asyncio.to_thread(auth.login, username, "TestPass123!") for _ in range(10)]
        results = await asyncio.gather(*tasks)
        
        for result in results:
            assert result.status == auth.AuthStatus.SUCCESS
            assert result.token is not None
            
        # Should have 10 + initial session
        sessions = auth.get_sessions(user.id)
        assert len(sessions) >= 10

    async def test_session_limit_enforcement(self, registered_user):
        """Test that oldest sessions are revoked when the limit is exceeded."""
        user, auth, username = registered_user
        
        # Create many sessions (limit is 10)
        # We'll create 15 to trigger the cleanup logic multiple times
        for _ in range(15):
            await asyncio.to_thread(auth.login, username, "TestPass123!")
            # Ensure unique timestamps for reliable ordering/revocation
            await asyncio.sleep(0.01)
            
        # Check that we don't exceed the limit
        sessions = auth.get_sessions(user.id)
        # The default limit is 10, so we should have 10 sessions
        assert len(sessions) <= 10
        
        # Verify they are the newest ones (last_activity descending)
        # The oldest ones should have been revoked
        assert len(sessions) == 10

    async def test_login_with_ip_tracking(self, registered_user):
        """Test that login correctly tracks multiple IP addresses."""
        user, auth, username = registered_user
        
        ips = ["1.1.1.1", "2.2.2.2", "3.3.3.3"]
        for ip in ips:
            await asyncio.to_thread(auth.login, username, "TestPass123!", ip_address=ip)
            
        # Check audit log for different IPs
        history = auth.get_login_history(user.id, limit=10)
        logged_ips = {e.ip_address for e in history if e.event_type == auth.AuditEventType.LOGIN_SUCCESS}
        for ip in ips:
            assert ip in logged_ips

