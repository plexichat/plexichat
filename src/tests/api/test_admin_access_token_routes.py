"""Tests for admin API access token management routes."""

import uuid


class TestAdminAccessTokenRoutes:
    """Ensure admin token management routes stay aligned with the dashboard."""

    def test_create_and_get_access_token_detail(
        self, test_client, monkeypatch, modules
    ):
        import src.api.routes.admin.security as security_routes

        monkeypatch.setattr(
            security_routes, "check_host_restriction", lambda request: None
        )
        monkeypatch.setattr(security_routes, "get_admin_from_token", lambda request: 1)

        unique_id = uuid.uuid4().hex[:8]
        create_response = test_client.post(
            "/api/v1/admin/security/access-tokens",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
            json={
                "name": f"Alpha {unique_id}",
                "description": "Cohort token",
                "scope_mode": "monitor",
            },
        )

        assert create_response.status_code == 200
        create_data = create_response.json()
        token_id = create_data["access_token"]["id"]
        raw_token = create_data["token"]

        modules.auth.verify_api_access_token(
            raw_token,
            ip_address="203.0.113.21",
            user_agent="pytest-admin-route",
            path="/api/v1/users/@me",
            method="GET",
        )

        detail_response = test_client.get(
            f"/api/v1/admin/security/access-tokens/{token_id}",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
        )

        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["access_token"]["description"] == "Cohort token"
        assert detail["access_token"]["scope_mode"] == "monitor"
        assert detail["access_token"]["use_count_total"] >= 1
        assert detail["distinct_ip_count"] == 1
        assert detail["top_ips"][0]["ip_address"] == "203.0.113.21"
        assert detail["recent_events"][0]["allowed"] is True

    def test_update_scope_rotate_and_revoke_access_token(
        self, test_client, monkeypatch
    ):
        import src.api.routes.admin.security as security_routes

        monkeypatch.setattr(
            security_routes, "check_host_restriction", lambda request: None
        )
        monkeypatch.setattr(security_routes, "get_admin_from_token", lambda request: 1)

        unique_id = uuid.uuid4().hex[:8]
        create_response = test_client.post(
            "/api/v1/admin/security/access-tokens",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
            json={"name": f"Rotate {unique_id}"},
        )
        token_id = create_response.json()["access_token"]["id"]

        update_response = test_client.patch(
            f"/api/v1/admin/security/access-tokens/{token_id}",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
            json={
                "description": "Updated description",
                "scope_mode": "enforce",
            },
        )

        assert update_response.status_code == 200
        assert update_response.json()["scope_mode"] == "enforce"

        scope_response = test_client.post(
            f"/api/v1/admin/security/access-tokens/{token_id}/scopes",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
            json={"scope_type": "cidr", "value": "198.51.100.0/24"},
        )

        assert scope_response.status_code == 200

        rotate_response = test_client.post(
            f"/api/v1/admin/security/access-tokens/{token_id}/rotate",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
            json={},
        )

        assert rotate_response.status_code == 200
        rotated = rotate_response.json()
        rotated_id = rotated["access_token"]["id"]
        assert rotated["access_token"]["description"] == "Updated description"
        assert rotated["access_token"]["scope_mode"] == "enforce"
        assert rotated["token"]

        rotated_detail = test_client.get(
            f"/api/v1/admin/security/access-tokens/{rotated_id}",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
        )

        assert rotated_detail.status_code == 200
        detail_data = rotated_detail.json()
        assert len(detail_data["scopes"]) == 1
        assert detail_data["scopes"][0]["value"] == "198.51.100.0/24"
        rotated_scope_id = detail_data["scopes"][0]["id"]

        remove_scope_response = test_client.delete(
            f"/api/v1/admin/security/access-tokens/{rotated_id}/scopes/{rotated_scope_id}",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
        )

        assert remove_scope_response.status_code == 200

        revoke_response = test_client.post(
            f"/api/v1/admin/security/access-tokens/{rotated_id}/revoke",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
        )

        assert revoke_response.status_code == 200

        list_response = test_client.get(
            "/api/v1/admin/security/access-tokens",
            headers={
                "Authorization": "Bearer admin-test-token"
            },  # pragma: allowlist secret
        )

        assert list_response.status_code == 200
        rotated_token = next(
            item for item in list_response.json() if item["id"] == rotated_id
        )
        assert rotated_token["revoked"] is True
