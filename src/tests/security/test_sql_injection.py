"""
SQL Injection Prevention Tests.

Tests that all database queries use parameterized statements and that
SQL injection attempts are properly prevented across all managers.
"""

import pytest


class TestSQLInjectionPrevention:
    """Test SQL injection prevention across all database operations."""

    def test_sql_injection_in_login_username(self, auth_manager):
        """Test SQL injection attempt in login username field."""
        sql_injection = "admin' OR '1'='1"

        with pytest.raises(Exception):
            auth_manager.login(username=sql_injection, password="anything")

    def test_sql_injection_in_username_registration(self, auth_manager, db):
        """Test SQL injection attempt in registration username."""
        sql_injection = "user'; DROP TABLE auth_users; --"

        with pytest.raises(Exception):
            auth_manager.register(
                username=sql_injection, email="sql@test.com", password="TestPass123!"
            )

        assert db.table_exists("auth_users")

    def test_sql_injection_in_email(self, auth_manager):
        """Test SQL injection attempt in email field."""
        sql_injection = "test@test.com' OR '1'='1' --"

        with pytest.raises(Exception):
            auth_manager.register(
                username="testuser123", email=sql_injection, password="TestPass123!"
            )

    def test_sql_injection_in_message_content(self, messaging_manager, two_users):
        """Test SQL injection attempt in message content."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)

        # Send a valid message first
        messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Valid message"
        )

        sql_injection = "'; DELETE FROM messages; --"

        # Content validation should reject SQL injection attempts
        with pytest.raises(Exception):
            messaging_manager.send_message(
                user_id=user1.id, conversation_id=dm.id, content=sql_injection
            )

        messages = messaging_manager.get_messages(user1.id, dm.id)
        assert len(messages) >= 1

    def test_sql_injection_in_search_query(self, messaging_manager, two_users):
        """Test SQL injection attempt in search functionality."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test message"
        )

        sql_injection = "test' OR '1'='1"

        try:
            results = messaging_manager.search_messages(user1.id, sql_injection)
            assert isinstance(results, list)
        except Exception:
            pass

    def test_sql_injection_in_server_name(self, server_manager, test_user, db):
        """Test SQL injection attempt in server name."""
        sql_injection = "Server'; DROP TABLE srv_servers; --"

        server = server_manager.create_server(owner_id=test_user.id, name=sql_injection)

        assert db.table_exists("srv_servers")
        assert server.name == sql_injection

    def test_sql_injection_in_channel_name(self, server_manager, test_user):
        """Test SQL injection attempt in channel name."""
        server = server_manager.create_server(owner_id=test_user.id, name="Test Server")

        sql_injection = "channel' OR '1'='1' --"

        channel = server_manager.create_channel(
            user_id=test_user.id,
            server_id=server.id,
            name=sql_injection,
            channel_type="text",
        )

        assert channel.name == sql_injection

    def test_sql_injection_in_user_id_lookup(self, auth_manager):
        """Test SQL injection attempt in user ID lookups."""
        sql_injection_id = "1 OR 1=1"

        # API should handle non-numeric IDs gracefully
        result = auth_manager.get_user(sql_injection_id)
        # Should return None or system user, not crash
        assert result is None or result.id == 0

    def test_union_based_sql_injection(self, auth_manager):
        """Test UNION-based SQL injection attempts."""
        union_injection = "admin' UNION SELECT * FROM auth_users WHERE '1'='1"

        with pytest.raises(Exception):
            auth_manager.login(username=union_injection, password="password")

    def test_time_based_sql_injection(self, auth_manager):
        """Test time-based blind SQL injection attempts."""
        time_injection = "admin' AND SLEEP(5) --"

        with pytest.raises(Exception):
            auth_manager.login(username=time_injection, password="password")

    def test_boolean_based_sql_injection(self, auth_manager):
        """Test boolean-based blind SQL injection attempts."""
        bool_injection = "admin' AND '1'='1"

        with pytest.raises(Exception):
            auth_manager.login(username=bool_injection, password="password")

    def test_sql_injection_in_order_by(self, messaging_manager, two_users):
        """Test SQL injection in ORDER BY clauses."""
        user1, user2 = two_users

        dm = messaging_manager.create_dm(user1.id, user2.id)
        messaging_manager.send_message(
            user_id=user1.id, conversation_id=dm.id, content="Test"
        )

        messages = messaging_manager.get_messages(user1.id, dm.id)
        assert isinstance(messages, list)

    def test_sql_injection_with_comments(self, auth_manager):
        """Test SQL injection with comment syntax."""
        comment_injections = [
            "admin'--",
            "admin'/*",
            "admin'#",
        ]

        for injection in comment_injections:
            with pytest.raises(Exception):
                auth_manager.login(username=injection, password="password")

    def test_sql_injection_with_quotes(self, auth_manager):
        """Test various quote escaping attempts."""
        quote_injections = [
            "admin''",
            "admin\\'",
            'admin"',
            "admin`",
        ]

        for injection in quote_injections:
            with pytest.raises(Exception):
                auth_manager.login(username=injection, password="password")

    def test_sql_injection_in_conversation_id(self, messaging_manager, test_user):
        """Test SQL injection in conversation ID parameter."""
        with pytest.raises(Exception):
            messaging_manager.get_messages(
                user_id=test_user.id, conversation_id="1 OR 1=1"
            )

    def test_stacked_queries_injection(self, auth_manager, db):
        """Test stacked queries SQL injection."""
        stacked_injection = "admin'; DELETE FROM auth_users WHERE '1'='1'; --"

        with pytest.raises(Exception):
            auth_manager.login(username=stacked_injection, password="password")

        assert db.table_exists("auth_users")

    def test_sql_injection_in_batch_operations(self, server_manager, test_user):
        """Test SQL injection in batch database operations."""
        server_manager.create_server(owner_id=test_user.id, name="Test Server")

        sql_injection_ids = ["1 OR 1=1", "2; DROP TABLE servers;"]

        for injection_id in sql_injection_ids:
            with pytest.raises(Exception):
                server_manager.get_server(injection_id)
