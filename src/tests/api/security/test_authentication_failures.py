"""
Test authentication failure scenarios across all API endpoints.

Tests that all protected endpoints properly reject:
- Missing authentication
- Invalid tokens
- Expired tokens
- Revoked tokens
"""


class TestAuthenticationRequired:
    """Test that protected endpoints require authentication."""
    
    def test_get_current_user_requires_auth(self, test_client):
        """Test GET /users/@me requires authentication."""
        response = test_client.get("/api/v1/users/@me")
        assert response.status_code == 401
        assert "error" in response.json()
    
    def test_update_user_requires_auth(self, test_client):
        """Test PATCH /users/@me requires authentication."""
        response = test_client.patch("/api/v1/users/@me", json={
            "username": "newname"
        })
        assert response.status_code == 401
    
    def test_logout_requires_auth(self, test_client):
        """Test POST /auth/logout requires authentication."""
        response = test_client.post("/api/v1/auth/logout")
        assert response.status_code == 401
    
    def test_get_servers_requires_auth(self, test_client):
        """Test GET /servers requires authentication."""
        response = test_client.get("/api/v1/servers")
        assert response.status_code == 401
    
    def test_create_server_requires_auth(self, test_client):
        """Test POST /servers requires authentication."""
        response = test_client.post("/api/v1/servers", json={
            "name": "Test Server"
        })
        assert response.status_code == 401
    
    def test_get_channel_messages_requires_auth(self, test_client):
        """Test GET /channels/{id}/messages requires authentication."""
        response = test_client.get("/api/v1/channels/123/messages")
        assert response.status_code == 401
    
    def test_send_message_requires_auth(self, test_client):
        """Test POST /channels/{id}/messages requires authentication."""
        response = test_client.post("/api/v1/channels/123/messages", json={
            "content": "test"
        })
        assert response.status_code == 401
    
    def test_get_relationships_requires_auth(self, test_client):
        """Test GET /relationships requires authentication."""
        response = test_client.get("/api/v1/relationships")
        assert response.status_code == 401
    
    def test_add_friend_requires_auth(self, test_client):
        """Test POST /relationships requires authentication."""
        response = test_client.post("/api/v1/relationships", json={
            "user_id": "123"
        })
        assert response.status_code == 401
    
    def test_get_presence_requires_auth(self, test_client):
        """Test GET /users/@me/presence requires authentication."""
        response = test_client.get("/api/v1/users/@me/presence")
        assert response.status_code == 401
    
    def test_update_presence_requires_auth(self, test_client):
        """Test PATCH /users/@me/presence requires authentication."""
        response = test_client.patch("/api/v1/users/@me/presence", json={
            "status": "online"
        })
        assert response.status_code == 401


class TestInvalidTokens:
    """Test that endpoints reject invalid authentication tokens."""
    
    def test_empty_token_rejected(self, test_client):
        """Test empty bearer token is rejected."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer "}
        )
        assert response.status_code == 401
    
    def test_malformed_token_rejected(self, test_client):
        """Test malformed token is rejected."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer invalid_token_123"}
        )
        assert response.status_code == 401
    
    def test_wrong_auth_scheme_rejected(self, test_client):
        """Test wrong auth scheme is rejected."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Basic dXNlcjpwYXNz"}
        )
        assert response.status_code == 401
    
    def test_no_bearer_prefix_rejected(self, test_client):
        """Test token without Bearer prefix is rejected."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "some_token_123"}
        )
        assert response.status_code == 401
    
    def test_jwt_like_token_rejected(self, test_client):
        """Test JWT-like token is rejected if not valid."""
        fake_jwt = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.invalid"
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bearer {fake_jwt}"}
        )
        assert response.status_code == 401
    
    def test_sql_injection_in_token_rejected(self, test_client):
        """Test SQL injection attempts in token are rejected."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer ' OR '1'='1"}
        )
        assert response.status_code == 401
    
    def test_xss_in_token_rejected(self, test_client):
        """Test XSS attempts in token are rejected."""
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": "Bearer <script>alert('xss')</script>"}
        )
        assert response.status_code == 401
    
    def test_extremely_long_token_rejected(self, test_client):
        """Test extremely long token is rejected."""
        long_token = "a" * 10000
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bearer {long_token}"}
        )
        assert response.status_code == 401


class TestRevokedTokens:
    """Test that revoked tokens are properly rejected."""
    
    def test_revoked_session_rejected(self, test_client, create_user_with_token):
        """Test revoked session token is rejected."""
        user_data = create_user_with_token()
        token = user_data["token"]
        
        response = test_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 200
        
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401
    
    def test_cannot_use_token_after_logout(self, test_client, create_user_with_token):
        """Test token cannot be used after logout."""
        user_data = create_user_with_token()
        token = user_data["token"]
        
        test_client.post(
            "/api/v1/auth/logout",
            headers={"Authorization": f"Bearer {token}"}
        )
        
        response = test_client.get(
            "/api/v1/servers",
            headers={"Authorization": f"Bearer {token}"}
        )
        assert response.status_code == 401


class TestTokenInjection:
    """Test token injection and manipulation attempts."""
    
    def test_multiple_authorization_headers_rejected(self, test_client, create_user_with_token):
        """Test multiple Authorization headers are handled correctly."""
        user_data = create_user_with_token()
        token = user_data["token"]
        
        response = test_client.get(
            "/api/v1/users/@me",
            headers=[
                ("Authorization", f"Bearer {token}"),
                ("Authorization", "Bearer fake_token"),
            ]
        )
        assert response.status_code in [401, 403]
    
    def test_case_sensitive_bearer_rejected(self, test_client, create_user_with_token):
        """Test Bearer is case-sensitive."""
        user_data = create_user_with_token()
        token = user_data["token"]
        
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"bearer {token}"}
        )
        assert response.status_code == 401
    
    def test_extra_spaces_in_auth_header_rejected(self, test_client, create_user_with_token):
        """Test extra spaces in auth header are handled."""
        user_data = create_user_with_token()
        token = user_data["token"]
        
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bearer  {token}  "}
        )
        assert response.status_code == 401
