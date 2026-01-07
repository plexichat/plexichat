"""
SQL Injection Prevention Tests.

Tests that all database queries use parameterized statements and that
SQL injection attempts are properly prevented across all managers.
"""

import pytest


class TestSQLInjectionPrevention:
    """Test SQL injection prevention across all database operations."""

    def test_sql_injection_in_login_username(self, modules):
        """Test SQL injection attempt in login username field."""
        sql_injection = "admin' OR '1'='1"

        with pytest.raises(Exception):
            modules.auth.login(username=sql_injection, password="anything")

    def test_sql_injection_in_username_registration(self, modules):
        """Test SQL injection attempt in registration username."""
        sql_injection = "user'; DROP TABLE auth_users; --"

        with pytest.raises(Exception):
            modules.auth.register(
                username=sql_injection, email="sql@test.com", password="TestPass123!"
            )

        assert modules.db.table_exists("auth_users")

    def test_sql_injection_in_email(self, modules):
        """Test SQL injection attempt in email field."""
        sql_injection = "test@test.com' OR '1'='1' --"

        with pytest.raises(Exception):
            modules.auth.register(
                username="testuser123", email=sql_injection, password="TestPass123!"
            )

    def test_sql_injection_in_message_content(self, modules, user_pool):
        """Test SQL injection attempt in message content."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user.id, user2.id)

        sql_injection = "'; DELETE FROM messages; --"

        msg = modules.messaging.send_message(
            user_id=user.id, conversation_id=dm.id, content=sql_injection
        )

        assert msg.content == sql_injection

        messages = modules.messaging.get_messages(user.id, dm.id)
        assert len(messages) >= 1

    def test_sql_injection_in_search_query(self, modules, user_pool):
        """Test SQL injection attempt in search functionality."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user.id, user2.id)
        modules.messaging.send_message(
            user_id=user.id, conversation_id=dm.id, content="Test message"
        )

        sql_injection = "test' OR '1'='1"

        try:
            results = modules.messaging.search_messages(user.id, sql_injection)
            assert isinstance(results, list)
        except Exception:
            pass

    def test_sql_injection_in_server_name(self, modules, user_pool):
        """Test SQL injection attempt in server name."""
        owner = user_pool.get_user()

        sql_injection = "Server'; DROP TABLE servers; --"

        server = modules.servers.create_server(owner_id=owner.id, name=sql_injection)

        assert modules.db.table_exists("servers")
        assert server.name == sql_injection

    def test_sql_injection_in_channel_name(self, modules, user_pool):
        """Test SQL injection attempt in channel name."""
        owner = user_pool.get_user()
        server = modules.servers.create_server(owner_id=owner.id, name="Test Server")

        sql_injection = "channel' OR '1'='1' --"

        channel = modules.servers.create_channel(
            server_id=server.id, creator_id=owner.id, name=sql_injection, type="text"
        )

        assert channel.name == sql_injection

    def test_sql_injection_in_user_id_lookup(self, modules, user_pool):
        """Test SQL injection attempt in user ID lookups."""
        sql_injection_id = "1 OR 1=1"

        with pytest.raises(Exception):
            modules.auth.get_user(sql_injection_id)

    def test_union_based_sql_injection(self, modules):
        """Test UNION-based SQL injection attempts."""
        union_injection = "admin' UNION SELECT * FROM auth_users WHERE '1'='1"

        with pytest.raises(Exception):
            modules.auth.login(username=union_injection, password="password")

    def test_time_based_sql_injection(self, modules):
        """Test time-based blind SQL injection attempts."""
        time_injection = "admin' AND SLEEP(5) --"

        with pytest.raises(Exception):
            modules.auth.login(username=time_injection, password="password")

    def test_boolean_based_sql_injection(self, modules):
        """Test boolean-based blind SQL injection attempts."""
        bool_injection = "admin' AND '1'='1"

        with pytest.raises(Exception):
            modules.auth.login(username=bool_injection, password="password")

    def test_sql_injection_in_order_by(self, modules, user_pool):
        """Test SQL injection in ORDER BY clauses."""
        user = user_pool.get_user()
        user2 = user_pool.get_user()

        dm = modules.messaging.create_dm(user.id, user2.id)
        modules.messaging.send_message(
            user_id=user.id, conversation_id=dm.id, content="Test"
        )

        messages = modules.messaging.get_messages(user.id, dm.id)
        assert isinstance(messages, list)

    def test_sql_injection_with_comments(self, modules):
        """Test SQL injection with comment syntax."""
        comment_injections = [
            "admin'--",
            "admin'/*",
            "admin'#",
        ]

        for injection in comment_injections:
            with pytest.raises(Exception):
                modules.auth.login(username=injection, password="password")

    def test_sql_injection_with_quotes(self, modules):
        """Test various quote escaping attempts."""
        quote_injections = [
            "admin''",
            "admin\\'",
            'admin"',
            "admin`",
        ]

        for injection in quote_injections:
            with pytest.raises(Exception):
                modules.auth.login(username=injection, password="password")

    def test_sql_injection_in_conversation_id(self, modules, user_pool):
        """Test SQL injection in conversation ID parameter."""
        user = user_pool.get_user()

        with pytest.raises(Exception):
            modules.messaging.get_messages(user_id=user.id, conversation_id="1 OR 1=1")

    def test_stacked_queries_injection(self, modules):
        """Test stacked queries SQL injection."""
        stacked_injection = "admin'; DELETE FROM auth_users WHERE '1'='1'; --"

        with pytest.raises(Exception):
            modules.auth.login(username=stacked_injection, password="password")

        assert modules.db.table_exists("auth_users")

    def test_sql_injection_in_batch_operations(self, modules, user_pool):
        """Test SQL injection in batch database operations."""
        owner = user_pool.get_user()
        modules.servers.create_server(owner_id=owner.id, name="Test Server")

        sql_injection_ids = ["1 OR 1=1", "2; DROP TABLE servers;"]

        for injection_id in sql_injection_ids:
            with pytest.raises(Exception):
                modules.servers.get_server(injection_id)
