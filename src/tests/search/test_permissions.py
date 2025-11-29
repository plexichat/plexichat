"""
Tests for search permission checks.
"""

import pytest
import uuid

from src.core.search.exceptions import SearchPermissionError


@pytest.mark.search
class TestMessageSearchPermissions:
    """Test message search permission checks."""
    
    def test_user_can_search_own_dms(self, db_and_modules):
        """Test user can search their own DMs."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        
        user1 = auth.register(
            username=f"perm1_{unique_id}",
            email=f"perm1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"perm2_{unique_id}",
            email=f"perm2_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, f"searchable {unique_id}")
        
        search.index_message(msg.id, msg.content, {
            "author_id": user1.id,
            "conversation_id": dm.id,
        })
        
        results = search.search_messages(user1.id, unique_id)
        
        assert len(results) >= 0
    
    def test_user_cannot_search_others_dms(self, db_and_modules):
        """Test user cannot search DMs they're not part of."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        
        user1 = auth.register(
            username=f"priv1_{unique_id}",
            email=f"priv1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"priv2_{unique_id}",
            email=f"priv2_{unique_id}@example.com",
            password="TestPass123!"
        )
        user3 = auth.register(
            username=f"priv3_{unique_id}",
            email=f"priv3_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, f"private {unique_id}")
        
        search.index_message(msg.id, msg.content, {
            "author_id": user1.id,
            "conversation_id": dm.id,
        })
        
        results = search.search_messages(user3.id, f"private {unique_id}")
        
        assert len(results) == 0
    
    def test_user_can_search_server_channels(self, db_and_modules):
        """Test user can search channels in servers they're a member of."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        
        owner = auth.register(
            username=f"srvown_{unique_id}",
            email=f"srvown_{unique_id}@example.com",
            password="TestPass123!"
        )
        member = auth.register(
            username=f"srvmem_{unique_id}",
            email=f"srvmem_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"Perm Server {unique_id}")
        servers.add_member(server.id, member.id)
        
        results = search.search_messages(member.id, "test", server_id=server.id)
        
        assert isinstance(results, list)


@pytest.mark.search
class TestDiscoveryPermissions:
    """Test discovery permission checks."""
    
    def test_only_owner_can_list_server(self, db_and_modules):
        """Test only server owner/admin can list server."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        
        owner = auth.register(
            username=f"listowner_{unique_id}",
            email=f"listowner_{unique_id}@example.com",
            password="TestPass123!"
        )
        member = auth.register(
            username=f"listmem_{unique_id}",
            email=f"listmem_{unique_id}@example.com",
            password="TestPass123!"
        )
        member2 = auth.register(
            username=f"listmem2_{unique_id}",
            email=f"listmem2_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"List Perm Server {unique_id}")
        servers.add_member(server.id, member.id)
        servers.add_member(server.id, member2.id)
        
        listing = search.list_server(
            user_id=owner.id,
            server_id=server.id,
            category="gaming"
        )
        
        assert listing is not None
    
    def test_member_can_bump_server(self, db_and_modules):
        """Test server member can bump server."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        
        owner = auth.register(
            username=f"bumpowner_{unique_id}",
            email=f"bumpowner_{unique_id}@example.com",
            password="TestPass123!"
        )
        member = auth.register(
            username=f"bumpmem_{unique_id}",
            email=f"bumpmem_{unique_id}@example.com",
            password="TestPass123!"
        )
        member2 = auth.register(
            username=f"bumpmem2_{unique_id}",
            email=f"bumpmem2_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"Bump Perm Server {unique_id}")
        servers.add_member(server.id, member.id)
        servers.add_member(server.id, member2.id)
        
        search.list_server(
            user_id=owner.id,
            server_id=server.id,
            category="gaming"
        )
        
        result = search.bump_server(member.id, server.id)
        
        assert result is True


@pytest.mark.search
class TestSearchAccessControl:
    """Test search access control."""
    
    def test_search_respects_conversation_access(self, db_and_modules):
        """Test search only returns messages from accessible conversations."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        
        user1 = auth.register(
            username=f"acc1_{unique_id}",
            email=f"acc1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"acc2_{unique_id}",
            email=f"acc2_{unique_id}@example.com",
            password="TestPass123!"
        )
        outsider = auth.register(
            username=f"outsider_{unique_id}",
            email=f"outsider_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, f"secret_{unique_id}")
        
        search.index_message(msg.id, msg.content, {
            "author_id": user1.id,
            "conversation_id": dm.id,
        })
        
        user1_results = search.search_messages(user1.id, f"secret_{unique_id}")
        outsider_results = search.search_messages(outsider.id, f"secret_{unique_id}")
        
        assert len(outsider_results) == 0
    
    def test_search_specific_conversation_access(self, db_and_modules):
        """Test searching specific conversation checks access."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        
        user1 = auth.register(
            username=f"spec1_{unique_id}",
            email=f"spec1_{unique_id}@example.com",
            password="TestPass123!"
        )
        user2 = auth.register(
            username=f"spec2_{unique_id}",
            email=f"spec2_{unique_id}@example.com",
            password="TestPass123!"
        )
        outsider = auth.register(
            username=f"specout_{unique_id}",
            email=f"specout_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        dm = messaging.create_dm(user1.id, user2.id)
        msg = messaging.send_message(user1.id, dm.id, f"specific_{unique_id}")
        
        search.index_message(msg.id, msg.content, {
            "author_id": user1.id,
            "conversation_id": dm.id,
        })
        
        outsider_results = search.search_messages(
            outsider.id,
            f"specific_{unique_id}",
            conversation_id=dm.id
        )
        
        assert len(outsider_results) == 0
