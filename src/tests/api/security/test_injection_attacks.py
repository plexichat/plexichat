"""Tests for injection attacks."""


def test_sql_injection_in_username(test_client):
    """Test that SQL injection in username is prevented."""
    sql_payload = "admin' OR '1'='1"
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": sql_payload, "password": "test"},
    )

    # The login boundary now refuses HTML metacharacters (the UIText policy),
    # so the payload never reaches the credential lookup and is rejected as
    # invalid input rather than as bad credentials. Both are safe refusals; what
    # matters is that the payload never authenticates.
    assert response.status_code in (400, 401)
    assert "access_token" not in response.text


def test_sql_injection_in_email(test_client):
    """Test that SQL injection in email is prevented."""
    sql_payload = "test@example.com' OR '1'='1"
    response = test_client.post(
        "/api/v1/auth/register",
        json={"username": "testuser", "email": sql_payload, "password": "TestPass123!"},
    )

    # Should fail validation
    assert response.status_code == 400


def test_command_injection(test_client):
    """Test that command injection is prevented."""
    cmd_payload = "test; rm -rf /"
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": cmd_payload, "password": "test"},
    )

    # Should fail authentication
    assert response.status_code == 401


def test_ldap_injection(test_client):
    """Test that LDAP injection is prevented."""
    ldap_payload = "admin)(|(password=*))"
    response = test_client.post(
        "/api/v1/auth/login",
        json={"username": ldap_payload, "password": "test"},
    )

    # Same boundary refusal as the SQL payload above: rejected as invalid input
    # because shell/LDAP metacharacters are not valid in a username, and never
    # authenticated.
    assert response.status_code in (400, 401)
    assert "access_token" not in response.text


def test_xss_in_username(test_client):
    """Test that XSS in username is sanitized."""
    xss_payload = "<script>alert('xss')</script>"
    response = test_client.post(
        "/api/v1/auth/register",
        json={
            "username": xss_payload,
            "email": "test@example.com",
            "password": "TestPass123!",
        },
    )

    # Should fail validation (username contains invalid characters)
    assert response.status_code == 400
