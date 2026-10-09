"""Tests for version and server-status routes."""

from src.api.routes import version as version_routes


def test_version_endpoint(test_client):
    """Test that version endpoint returns version information."""
    response = test_client.get("/api/v1/version")
    assert response.status_code == 200
    data = response.json()
    assert "version" in data


def test_version_endpoint_no_auth(test_client):
    """Test that version endpoint works without authentication."""
    response = test_client.get("/api/v1/version")
    assert response.status_code == 200


def test_converting_a_version_does_not_poison_the_server_cache():
    """Converting an arbitrary Version must not overwrite the cached server info.

    ``_version_to_info`` used to assign to the module-level
    ``_cached_version_info`` on every call, so converting the *client's* or the
    *minimum supported* version silently replaced the cached *server* version.
    ``/status`` then reported whatever version was converted last.
    """
    server = version_routes.version_util.parse("r.1.2-3")
    client = version_routes.version_util.parse("a.9.9-99")

    version_routes._cached_version_info = None

    server_info = version_routes._version_to_info(server)
    assert version_routes._cached_version_info is None

    # Converting the client's version must leave the cache alone.
    client_info = version_routes._version_to_info(client)
    assert version_routes._cached_version_info is None

    # And the converted objects themselves are still correct.
    assert server_info.string == "r.1.2-3"
    assert client_info.string == "a.9.9-99"


def test_status_reports_the_server_version_after_negotiation():
    """``/status`` must report the server version, not the last client version."""
    server = version_routes.version_util.current_string()

    version_routes._cached_version_info = None
    try:
        # Simulate a negotiate call having converted some other version.
        version_routes._version_to_info(version_routes.version_util.parse("a.1.0-1"))
        status = version_routes.get_server_status()
        assert status.version.string == server
    finally:
        version_routes._cached_version_info = None
