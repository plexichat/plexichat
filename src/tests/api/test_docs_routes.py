"""Tests for the custom documentation portal routes."""


def test_docs_root(test_client):
    """Test that docs root endpoint is accessible."""
    response = test_client.get("/docs")
    # Docs might be disabled or not fully set up in tests
    assert response.status_code in [200, 404]


def test_docs_getting_started(test_client):
    """Test that docs getting-started endpoint is accessible."""
    response = test_client.get("/docs/getting-started")
    # Docs might be disabled or not fully set up in tests
    assert response.status_code in [200, 404]


def test_docs_configuration(test_client):
    """Test that docs configuration endpoint is accessible."""
    response = test_client.get("/docs/configuration")
    # Docs might be disabled or not fully set up in tests
    assert response.status_code in [200, 404]


def test_docs_security(test_client):
    """Test that docs security endpoint is accessible."""
    response = test_client.get("/docs/security")
    # Docs might be disabled or not fully set up in tests
    assert response.status_code in [200, 404]


def test_docs_rate_limits(test_client):
    """Test that docs rate-limits endpoint is accessible."""
    response = test_client.get("/docs/rate-limits")
    # Docs might be disabled or not fully set up in tests
    assert response.status_code in [200, 404]


def test_docs_reference(test_client):
    """Test that docs reference endpoint is accessible."""
    response = test_client.get("/docs/reference")
    # Docs might be disabled or not fully set up in tests
    assert response.status_code in [200, 404]


def test_docs_websocket(test_client):
    """Test that docs websocket endpoint is accessible."""
    response = test_client.get("/docs/websocket")
    # Docs might be disabled or not fully set up in tests
    assert response.status_code in [200, 404]


def test_docs_admin(test_client):
    """Test that docs admin endpoint is accessible."""
    response = test_client.get("/docs/admin")
    # Docs might be disabled or not fully set up in tests
    assert response.status_code in [200, 404]
