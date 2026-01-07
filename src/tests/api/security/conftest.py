"""
Shared fixtures for API security tests.
"""

import pytest
import uuid


@pytest.fixture
def malicious_payloads():
    """Common malicious payloads for injection testing."""
    return {
        "sql_injection": [
            "' OR '1'='1",
            "1'; DROP TABLE users--",
            "1' UNION SELECT * FROM auth_users--",
            "admin'--",
            "' OR 1=1--",
        ],
        "xss": [
            "<script>alert('XSS')</script>",
            "<img src=x onerror=alert('XSS')>",
            "javascript:alert('XSS')",
            "<svg onload=alert('XSS')>",
        ],
        "path_traversal": [
            "../../../etc/passwd",
            "..\\..\\..\\windows\\system32",
            "....//....//etc/passwd",
            "%2e%2e%2f%2e%2e%2f",
        ],
        "header_injection": [
            "test\r\nX-Injected: header",
            "test\nSet-Cookie: session=hijacked",
            "test\r\n\r\nHTTP/1.1 200 OK",
        ],
        "command_injection": [
            "; ls -la",
            "| cat /etc/passwd",
            "& whoami",
            "`id`",
            "$(whoami)",
        ],
    }


@pytest.fixture
def invalid_tokens():
    """Invalid authentication tokens for testing."""
    return [
        "",
        "invalid_token",
        "Bearer",
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.invalid.signature",
        "a" * 1000,
        "../../../etc/passwd",
        "<script>alert('xss')</script>",
        "' OR '1'='1",
    ]


@pytest.fixture
def create_user_with_token(modules):
    """Factory fixture to create users with authentication tokens."""

    def _create():
        unique_id = uuid.uuid4().hex[:16]
        username = f"sectest_{unique_id}"
        email = f"{username}@test.example.com"
        password = "SecurePass123!"

        user = modules.auth.register(username=username, email=email, password=password)

        result = modules.auth.login(username, password)

        return {
            "user": user,
            "username": username,
            "password": password,
            "token": result.token,
        }

    return _create


@pytest.fixture
def create_server_with_owner(modules, create_user_with_token):
    """Factory fixture to create server with owner."""

    def _create():
        owner = create_user_with_token()
        server = modules.servers.create_server(
            owner_id=owner["user"].id, name=f"Test Server {uuid.uuid4().hex[:6]}"
        )
        channels = modules.servers.get_channels(owner["user"].id, server.id)
        return {
            "server": server,
            "owner": owner,
            "channel": channels[0] if channels else None,
        }

    return _create
