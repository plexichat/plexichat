"""
Test injection attack prevention.

Tests that the API properly prevents:
- SQL injection
- NoSQL injection
- Command injection
- LDAP injection
- XPath injection
"""


class TestSQLInjection:
    """Test SQL injection prevention."""

    def test_sql_injection_in_login_username(self, test_client):
        """Test SQL injection in login username."""
        payloads = [
            "' OR '1'='1",
            "admin'--",
            "' OR 1=1--",
            "1' UNION SELECT * FROM auth_users--",
        ]

        for payload in payloads:
            response = test_client.post(
                "/api/v1/auth/login",
                json={"username": payload, "password": "TestPass123!"},
            )

            assert response.status_code == 401, (
                f"SQL injection payload should be rejected: {payload}"
            )

    def test_sql_injection_in_search(self, test_client, create_user_with_token):
        """Test SQL injection in user search."""
        user = create_user_with_token()

        payloads = [
            "' OR '1'='1",
            "admin' OR 1=1--",
            "' UNION SELECT password FROM auth_users--",
        ]

        for payload in payloads:
            response = test_client.get(
                f"/api/v1/users/search?username={payload}",
                headers={"Authorization": f"Bearer {user['token']}"},
            )

            assert response.status_code in [
                400,
                404,
                422,
            ], f"SQL injection in search should be handled: {payload}"

    def test_sql_injection_in_message_content(
        self, test_client, modules, create_user_with_token
    ):
        """Test SQL injection in message content."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()

        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)

        payloads = [
            "'; DROP TABLE messages;--",
            "' OR '1'='1",
            "1' UNION SELECT * FROM auth_users--",
        ]

        for payload in payloads:
            response = test_client.post(
                f"/api/v1/channels/{dm.id}/messages",
                headers={"Authorization": f"Bearer {user1['token']}"},
                json={"content": payload},
            )

            if response.status_code in [200, 201]:
                data = response.json()
                assert data["content"] == payload


class TestNoSQLInjection:
    """Test NoSQL injection prevention."""

    def test_nosql_injection_in_login(self, test_client):
        """Test NoSQL injection in login."""
        response = test_client.post(
            "/api/v1/auth/login",
            json={"username": {"$ne": None}, "password": {"$ne": None}},
        )

        assert response.status_code in [400, 422], "NoSQL injection should be rejected"

    def test_nosql_injection_with_operators(self, test_client, create_user_with_token):
        """Test NoSQL operators in queries."""
        user = create_user_with_token()

        response = test_client.get(
            "/api/v1/users/search?username[%24ne]=null",
            headers={"Authorization": f"Bearer {user['token']}"},
        )

        assert response.status_code in [400, 404, 422]


class TestCommandInjection:
    """Test command injection prevention."""

    def test_command_injection_in_username(self, test_client):
        """Test command injection in username."""
        payloads = [
            "; ls -la",
            "| cat /etc/passwd",
            "& whoami",
            "`id`",
            "$(whoami)",
        ]

        for payload in payloads:
            response = test_client.post(
                "/api/v1/auth/register",
                json={
                    "username": f"user{payload}",
                    "email": f"test_{id(payload)}@test.com",
                    "password": "TestPass123!",
                },
            )

            assert response.status_code in [
                400,
                422,
            ], f"Command injection should be rejected: {payload}"

    def test_command_injection_in_server_name(
        self, test_client, create_user_with_token
    ):
        """Test command injection in server name."""
        user = create_user_with_token()

        payloads = [
            "Server; rm -rf /",
            "Server | cat /etc/passwd",
            "Server `whoami`",
        ]

        for payload in payloads:
            response = test_client.post(
                "/api/v1/servers",
                headers={"Authorization": f"Bearer {user['token']}"},
                json={"name": payload},
            )

            if response.status_code in [200, 201]:
                data = response.json()
                assert data["name"] == payload


class TestXSSPrevention:
    """Test XSS prevention."""

    def test_xss_in_message_content(self, test_client, modules, create_user_with_token):
        """Test XSS in message content is stored safely."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()

        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)

        xss_payloads = [
            "<script>alert('XSS')</script>",
            "<img src=x onerror=alert('XSS')>",
            "<svg onload=alert('XSS')>",
            "javascript:alert('XSS')",
        ]

        for payload in xss_payloads:
            response = test_client.post(
                f"/api/v1/channels/{dm.id}/messages",
                headers={"Authorization": f"Bearer {user1['token']}"},
                json={"content": payload},
            )

            if response.status_code in [200, 201]:
                data = response.json()
                assert "content" in data

    def test_xss_in_username(self, test_client):
        """Test XSS in username."""
        response = test_client.post(
            "/api/v1/auth/register",
            json={
                "username": "<script>alert('xss')</script>",
                "email": f"test_{id(self)}@test.com",
                "password": "TestPass123!",
            },
        )

        assert response.status_code in [400, 422]

    def test_xss_in_server_name(self, test_client, create_user_with_token):
        """Test XSS in server name is handled."""
        user = create_user_with_token()

        response = test_client.post(
            "/api/v1/servers",
            headers={"Authorization": f"Bearer {user['token']}"},
            json={"name": "<script>alert('xss')</script>"},
        )

        if response.status_code in [200, 201]:
            data = response.json()
            assert "name" in data


class TestPathTraversal:
    """Test path traversal prevention."""

    def test_path_traversal_in_username(self, test_client):
        """Test path traversal in username."""
        payloads = [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "....//....//etc/passwd",
        ]

        for payload in payloads:
            response = test_client.post(
                "/api/v1/auth/register",
                json={
                    "username": payload,
                    "email": f"test_{id(payload)}@test.com",
                    "password": "TestPass123!",
                },
            )

            assert response.status_code in [
                400,
                422,
            ], f"Path traversal should be rejected: {payload}"

    def test_path_traversal_in_file_access(self, test_client, create_user_with_token):
        """Test path traversal in file access."""
        user = create_user_with_token()

        payloads = [
            "../../../etc/passwd",
            "..%2F..%2F..%2Fetc%2Fpasswd",
            "....//....//etc/passwd",
        ]

        for payload in payloads:
            response = test_client.get(
                f"/api/v1/media/attachments/{payload}",
                headers={"Authorization": f"Bearer {user['token']}"},
            )

            assert response.status_code in [
                400,
                403,
                404,
            ], f"Path traversal should be prevented: {payload}"


class TestLDAPInjection:
    """Test LDAP injection prevention."""

    def test_ldap_injection_in_username(self, test_client):
        """Test LDAP injection in username."""
        payloads = [
            "*",
            "*)(uid=*",
            "admin)(&(password=*",
        ]

        for payload in payloads:
            response = test_client.post(
                "/api/v1/auth/login",
                json={"username": payload, "password": "TestPass123!"},
            )

            assert response.status_code == 401, (
                f"LDAP injection should be rejected: {payload}"
            )


class TestXPathInjection:
    """Test XPath injection prevention."""

    def test_xpath_injection_in_search(self, test_client, create_user_with_token):
        """Test XPath injection in search queries."""
        user = create_user_with_token()

        payloads = [
            "' or '1'='1",
            "'] | //user/*[1] | //['1",
            "' or 1=1 or ''='",
        ]

        for payload in payloads:
            response = test_client.get(
                f"/api/v1/users/search?username={payload}",
                headers={"Authorization": f"Bearer {user['token']}"},
            )

            assert response.status_code in [400, 404, 422]
