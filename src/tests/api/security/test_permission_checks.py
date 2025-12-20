"""
Test permission and authorization checks across all API endpoints.

Tests that users cannot:
- Access resources they don't own
- Modify resources they don't have permission for
- View private data of other users
- Perform admin actions without admin privileges
"""

import pytest


class TestResourceOwnership:
    """Test that users can only access resources they own or have permission to."""
    
    def test_cannot_access_other_user_dms(self, test_client, create_user_with_token):
        """Test user cannot access another user's DM conversations."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        user3 = create_user_with_token()
        
        dm_response = test_client.post(
            "/api/v1/users/@me/channels",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={"recipient_id": str(user2["user"].id)}
        )
        
        if dm_response.status_code == 200:
            dm_id = dm_response.json()["id"]
            
            response = test_client.get(
                f"/api/v1/channels/{dm_id}/messages",
                headers={"Authorization": f"Bearer {user3['token']}"}
            )
            assert response.status_code in [403, 404]
    
    def test_cannot_delete_other_user_messages(self, test_client, modules, create_user_with_token):
        """Test user cannot delete messages from other users."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        msg = modules.messaging.send_message(
            user_id=user1["user"].id,
            conversation_id=dm.id,
            content="Test message"
        )
        
        response = test_client.delete(
            f"/api/v1/channels/{dm.id}/messages/{msg.id}",
            headers={"Authorization": f"Bearer {user2['token']}"}
        )
        assert response.status_code in [403, 404]
    
    def test_cannot_modify_other_user_profile(self, test_client, create_user_with_token):
        """Test user cannot modify another user's profile."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        response = test_client.patch(
            f"/api/v1/users/{user2['user'].id}",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={"username": "hacked"}
        )
        assert response.status_code in [403, 404, 405]
    
    def test_cannot_delete_other_user_server(self, test_client, create_server_with_owner, create_user_with_token):
        """Test user cannot delete servers they don't own."""
        server_data = create_server_with_owner()
        other_user = create_user_with_token()
        
        response = test_client.delete(
            f"/api/v1/servers/{server_data['server'].id}",
            headers={"Authorization": f"Bearer {other_user['token']}"}
        )
        assert response.status_code in [403, 404]
    
    def test_cannot_kick_members_without_permission(self, test_client, modules, create_server_with_owner, create_user_with_token):
        """Test regular members cannot kick other members."""
        server_data = create_server_with_owner()
        member1 = create_user_with_token()
        member2 = create_user_with_token()
        
        modules.servers.add_member(server_data['server'].id, member1["user"].id)
        modules.servers.add_member(server_data['server'].id, member2["user"].id)
        
        response = test_client.delete(
            f"/api/v1/servers/{server_data['server'].id}/members/{member2['user'].id}",
            headers={"Authorization": f"Bearer {member1['token']}"}
        )
        assert response.status_code in [403, 404, 405]


class TestPrivateDataAccess:
    """Test that private data is properly protected."""
    
    def test_cannot_view_other_user_email(self, test_client, create_user_with_token):
        """Test user email is not exposed to other users."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        response = test_client.get(
            f"/api/v1/users/{user2['user'].id}",
            headers={"Authorization": f"Bearer {user1['token']}"}
        )
        
        if response.status_code == 200:
            data = response.json()
            assert "email" not in data or data["email"] is None
    
    def test_cannot_view_other_user_sessions(self, test_client, create_user_with_token):
        """Test user cannot view another user's active sessions."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        response = test_client.get(
            f"/api/v1/users/{user2['user'].id}/sessions",
            headers={"Authorization": f"Bearer {user1['token']}"}
        )
        assert response.status_code in [403, 404, 405]
    
    def test_cannot_view_other_user_2fa_status(self, test_client, create_user_with_token):
        """Test user cannot view another user's 2FA status."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        response = test_client.get(
            f"/api/v1/users/{user2['user'].id}/2fa/status",
            headers={"Authorization": f"Bearer {user1['token']}"}
        )
        assert response.status_code in [403, 404, 405]
    
    def test_own_email_visible_to_self(self, test_client, create_user_with_token):
        """Test user can see their own email."""
        user = create_user_with_token()
        
        response = test_client.get(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bearer {user['token']}"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data.get("email") == user["user"].email


class TestServerPermissions:
    """Test server-specific permission checks."""
    
    def test_non_member_cannot_view_server_channels(self, test_client, create_server_with_owner, create_user_with_token):
        """Test non-members cannot view server channels."""
        server_data = create_server_with_owner()
        non_member = create_user_with_token()
        
        response = test_client.get(
            f"/api/v1/servers/{server_data['server'].id}/channels",
            headers={"Authorization": f"Bearer {non_member['token']}"}
        )
        assert response.status_code in [403, 404]
    
    def test_non_member_cannot_send_messages_in_server(self, test_client, create_server_with_owner, create_user_with_token):
        """Test non-members cannot send messages in server channels."""
        server_data = create_server_with_owner()
        non_member = create_user_with_token()
        
        if server_data['channel']:
            response = test_client.post(
                f"/api/v1/channels/{server_data['channel'].id}/messages",
                headers={"Authorization": f"Bearer {non_member['token']}"},
                json={"content": "Unauthorized message"}
            )
            assert response.status_code in [403, 404]
    
    def test_non_member_cannot_view_server_members(self, test_client, create_server_with_owner, create_user_with_token):
        """Test non-members cannot view server member list."""
        server_data = create_server_with_owner()
        non_member = create_user_with_token()
        
        response = test_client.get(
            f"/api/v1/servers/{server_data['server'].id}/members",
            headers={"Authorization": f"Bearer {non_member['token']}"}
        )
        assert response.status_code in [403, 404]


class TestCrossUserOperations:
    """Test operations that cross user boundaries."""
    
    def test_cannot_accept_relationship_for_another_user(self, test_client, modules, create_user_with_token):
        """Test user cannot accept friend requests on behalf of others."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        user3 = create_user_with_token()
        
        try:
            modules.relationships.send_friend_request(user1["user"].id, user2["user"].id)
        except Exception:
            pass
        
        response = test_client.put(
            f"/api/v1/relationships/{user1['user'].id}",
            headers={"Authorization": f"Bearer {user3['token']}"}
        )
        assert response.status_code in [403, 404, 405]
    
    def test_cannot_block_on_behalf_of_another_user(self, test_client, create_user_with_token):
        """Test user cannot block users on behalf of others."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        user3 = create_user_with_token()
        
        response = test_client.post(
            "/api/v1/relationships/block",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={"user_id": str(user3["user"].id), "on_behalf_of": str(user2["user"].id)}
        )
        
        if response.status_code == 200:
            pytest.fail("User was able to block on behalf of another user")


class TestParameterTampering:
    """Test that users cannot tamper with parameters to escalate privileges."""
    
    def test_cannot_set_self_as_admin_via_update(self, test_client, create_user_with_token):
        """Test user cannot set themselves as admin through profile update."""
        user = create_user_with_token()
        
        response = test_client.patch(
            "/api/v1/users/@me",
            headers={"Authorization": f"Bearer {user['token']}"},
            json={
                "is_admin": True,
                "admin": True,
                "role": "admin",
                "permissions": {"admin": True}
            }
        )
        
        if response.status_code == 200:
            response = test_client.get(
                "/api/v1/users/@me",
                headers={"Authorization": f"Bearer {user['token']}"}
            )
            data = response.json()
            assert not data.get("is_admin", False)
    
    def test_cannot_change_user_id_in_message(self, test_client, modules, create_user_with_token):
        """Test user cannot send messages as another user."""
        user1 = create_user_with_token()
        user2 = create_user_with_token()
        
        dm = modules.messaging.create_dm(user1["user"].id, user2["user"].id)
        
        response = test_client.post(
            f"/api/v1/channels/{dm.id}/messages",
            headers={"Authorization": f"Bearer {user1['token']}"},
            json={
                "content": "Test",
                "author_id": str(user2["user"].id),
                "user_id": str(user2["user"].id)
            }
        )
        
        if response.status_code == 200:
            data = response.json()
            assert str(data["author_id"]) == str(user1["user"].id)
    
    def test_cannot_transfer_server_ownership_without_permission(self, test_client, create_server_with_owner, create_user_with_token):
        """Test user cannot transfer server ownership without being owner."""
        server_data = create_server_with_owner()
        other_user = create_user_with_token()
        
        response = test_client.patch(
            f"/api/v1/servers/{server_data['server'].id}",
            headers={"Authorization": f"Bearer {other_user['token']}"},
            json={"owner_id": str(other_user["user"].id)}
        )
        assert response.status_code in [403, 404]
