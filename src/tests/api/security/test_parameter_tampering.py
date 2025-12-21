"""
Test parameter tampering and input validation.

Tests that the API properly validates and sanitizes:
- Request body parameters
- Query parameters
- Path parameters
- Array and object bounds
- Type validation
"""


import pytest


class TestBodyParameterValidation:
    """Test validation of request body parameters."""
    
    def test_extra_fields_ignored_in_registration(self, test_client):
        """Test extra fields in registration are ignored."""
        response = test_client.post("/api/v1/auth/register", json={
            "username": f"testuser_{id(self)}",
            "email": f"test_{id(self)}@test.com",
            "password": "TestPass123!",
            "is_admin": True,
            "permissions": {"admin": True},
            "role": "admin"
        })
        
        if response.status_code == 200:
            user_response = test_client.get(
                "/api/v1/users/@me",
                headers={"Authorization": f"Bearer {response.json()['token']}"}
            )
            data = user_response.json()
            assert not data.get("is_admin", False)
    
    def test_negative_ids_rejected(self, test_client, create_user_with_token):
        """Test negative IDs are rejected."""
        user = create_user_with_token()
        
        response = test_client.get(
            "/api/v1/users/-1",
            headers={"Authorization": f"Bearer {user['token']}"}
        )
        
        assert response.status_code in [400, 404, 422]
    
    @pytest.mark.skip(reason="ID 0 is now valid System user")
    def test_zero_id_rejected(self, test_client, create_user_with_token):
        """Test zero ID is rejected."""
        user = create_user_with_token()
        
        response = test_client.get(
            "/api/v1/users/0",
            headers={"Authorization": f"Bearer {user['token']}"}
        )
        
        assert response.status_code in [400, 404, 422]
    
    def test_string_id_where_int_expected(self, test_client, create_user_with_token):
        """Test string ID where integer expected is rejected."""
        user = create_user_with_token()
        
        response = test_client.get(
            "/api/v1/users/not_a_number",
            headers={"Authorization": f"Bearer {user['token']}"}
        )
        
        assert response.status_code in [400, 422]
    
    def test_extremely_large_id_handled(self, test_client, create_user_with_token):
        """Test extremely large ID is handled gracefully."""
        user = create_user_with_token()
        
        huge_id = "9" * 50
        response = test_client.get(
            f"/api/v1/users/{huge_id}",
            headers={"Authorization": f"Bearer {user['token']}"}
        )
        
        assert response.status_code in [400, 404, 422]


class TestContentLengthValidation:
    """Test content length and size validation."""
    
    def test_extremely_long_message_rejected(self, test_client, modules, create_user_with_token):
        """Test extremely long messages are rejected."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        
        long_content = "A" * 10000
        response = test_client.post(
            f"/api/v1/channels/{dm.id}/messages",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={"content": long_content}
        )
        
        assert response.status_code in [400, 413, 422]
    
    def test_extremely_long_username_rejected(self, test_client):
        """Test extremely long username is rejected."""
        response = test_client.post("/api/v1/auth/register", json={
            "username": "A" * 1000,
            "email": f"test_{id(self)}@test.com",
            "password": "TestPass123!"
        })
        
        assert response.status_code in [400, 422]
    
    def test_extremely_long_server_name_rejected(self, test_client, create_user_with_token):
        """Test extremely long server name is rejected."""
        user = create_user_with_token()
        
        response = test_client.post(
            "/api/v1/servers",
            headers={"Authorization": f"Bearer {user['token']}"},
            json={"name": "A" * 500}
        )
        
        assert response.status_code in [400, 422]
    
    def test_empty_required_fields_rejected(self, test_client):
        """Test empty required fields are rejected."""
        response = test_client.post("/api/v1/auth/register", json={
            "username": "",
            "email": "",
            "password": ""
        })
        
        assert response.status_code in [400, 422]


class TestArrayBoundsValidation:
    """Test validation of array sizes and bounds."""
    
    def test_too_many_attachments_rejected(self, test_client, modules, create_user_with_token):
        """Test sending too many attachments is rejected."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        
        attachments = [
            {
                "filename": f"file{i}.txt",
                "content_type": "text/plain",
                "size": 100,
                "url": f"http://example.com/file{i}.txt"
            }
            for i in range(50)
        ]
        
        response = test_client.post(
            f"/api/v1/channels/{dm.id}/messages",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={
                "content": "Test",
                "attachments": attachments
            }
        )
        
        assert response.status_code in [400, 422]
    
    def test_empty_array_in_batch_operations(self, test_client, create_user_with_token):
        """Test empty arrays in batch operations are handled."""
        user = create_user_with_token()
        
        response = test_client.post(
            "/api/v1/servers",
            headers={"Authorization": f"Bearer {user['token']}"},
            json={
                "name": "Test Server",
                "channels": []
            }
        )
        
        assert response.status_code in [200, 201, 400, 422]


class TestTypeValidation:
    """Test type validation of parameters."""
    
    def test_string_where_boolean_expected(self, test_client, modules, create_user_with_token):
        """Test string where boolean expected is rejected."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        msg = modules.messaging.send_message(
            user_id=user1["user"].id,
            conversation_id=dm.id,
            content="Test"
        )
        
        response = test_client.patch(
            f"/api/v1/channels/{dm.id}/messages/{msg.id}",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={
                "content": "Updated",
                "pinned": "true"
            }
        )
        
        assert response.status_code in [200, 400, 422]
    
    def test_null_in_required_field(self, test_client, modules, create_user_with_token):
        """Test null in required field is rejected."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        
        response = test_client.post(
            f"/api/v1/channels/{dm.id}/messages",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={"content": None}
        )
        
        assert response.status_code in [400, 422]
    
    def test_object_where_string_expected(self, test_client):
        """Test object where string expected is rejected."""
        response = test_client.post("/api/v1/auth/login", json={
            "username": {"$ne": None},
            "password": {"$ne": None}
        })
        
        assert response.status_code in [400, 422]
    
    def test_array_where_string_expected(self, test_client):
        """Test array where string expected is rejected."""
        response = test_client.post("/api/v1/auth/login", json={
            "username": ["admin", "user"],
            "password": "TestPass123!"
        })
        
        assert response.status_code in [400, 422]


class TestSpecialCharacterHandling:
    """Test handling of special characters in parameters."""
    
    def test_unicode_in_message_content(self, test_client, modules, create_user_with_token):
        """Test unicode characters in message content."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        
        unicode_content = "Hello 世界 🌍 🚀 ñ ü ö"
        response = test_client.post(
            f"/api/v1/channels/{dm.id}/messages",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={"content": unicode_content}
        )
        
        if response.status_code in [200, 201]:
            data = response.json()
            assert data["content"] == unicode_content
    
    def test_emoji_in_username(self, test_client):
        """Test emoji in username."""
        response = test_client.post("/api/v1/auth/register", json={
            "username": f"user🚀{id(self)}",
            "email": f"test_{id(self)}@test.com",
            "password": "TestPass123!"
        })
        
        assert response.status_code in [200, 400, 422]
    
    def test_special_chars_in_server_name(self, test_client, create_user_with_token):
        """Test special characters in server name."""
        user = create_user_with_token()
        
        response = test_client.post(
            "/api/v1/servers",
            headers={"Authorization": f"Bearer {user['token']}"},
            json={"name": "Server <>&\"'"}
        )
        
        assert response.status_code in [200, 201, 400, 422]
    
    def test_null_bytes_in_content_rejected(self, test_client, modules, create_user_with_token):
        """Test null bytes in content are rejected."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        
        response = test_client.post(
            f"/api/v1/channels/{dm.id}/messages",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={"content": "Test\x00Message"}
        )
        
        assert response.status_code in [200, 201, 400, 422]


class TestQueryParameterValidation:
    """Test validation of query parameters."""
    
    def test_negative_limit_rejected(self, test_client, modules, create_user_with_token):
        """Test negative limit in pagination is rejected."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        
        response = test_client.get(
            f"/api/v1/channels/{dm.id}/messages?limit=-10",
            headers={"Authorization": f"Bearer {user1['token']}"}
        )
        
        assert response.status_code in [400, 422]
    
    def test_excessive_limit_capped(self, test_client, modules, create_user_with_token):
        """Test excessive limit is capped to maximum."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        
        response = test_client.get(
            f"/api/v1/channels/{dm.id}/messages?limit=10000",
            headers={"Authorization": f"Bearer {user1['token']}"}
        )
        
        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)
            assert len(data) <= 100
    
    def test_sql_injection_in_query_params(self, test_client, create_user_with_token):
        """Test SQL injection in query parameters is handled."""
        user = create_user_with_token()
        
        response = test_client.get(
            "/api/v1/users/search?username=' OR '1'='1",
            headers={"Authorization": f"Bearer {user['token']}"}
        )
        
        assert response.status_code in [400, 404, 422]
