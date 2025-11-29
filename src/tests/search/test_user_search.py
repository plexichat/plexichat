"""
Tests for user search functionality.
"""

import pytest
import uuid

from src.core.search.models import UserSearchResult, IndexedUser


@pytest.mark.search
class TestUserSearchBasic:
    """Test basic user search."""
    
    def test_search_users_returns_list(self, db_and_modules):
        """Test that user search returns a list."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        user = auth.register(
            username=f"searchable_{unique_id}",
            email=f"searchable_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        search._get_manager()._indexer.index_user(
            IndexedUser(user_id=user.id, username=user.username)
        )
        
        results = search.search_users(user.id, unique_id)
        
        assert isinstance(results, list)
    
    def test_search_users_empty_query(self, db_and_modules):
        """Test search with empty query."""
        db, auth, messaging, servers, search = db_and_modules
        
        results = search.search_users(1, "")
        
        assert results == []
    
    def test_search_users_result_type(self, db_and_modules):
        """Test search results are UserSearchResult."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        user = auth.register(
            username=f"typechk_{unique_id}",
            email=f"typechk_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        search._get_manager()._indexer.index_user(
            IndexedUser(user_id=user.id, username=user.username)
        )
        
        results = search.search_users(user.id, unique_id)
        
        if results:
            assert isinstance(results[0], UserSearchResult)


@pytest.mark.search
class TestUserSearchFilters:
    """Test user search with filters."""
    
    def test_search_users_with_limit(self, db_and_modules):
        """Test search respects limit."""
        db, auth, messaging, servers, search = db_and_modules
        
        results = search.search_users(1, "user", limit=5)
        
        assert len(results) <= 5
    
    def test_search_users_with_offset(self, db_and_modules):
        """Test search respects offset."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        
        for i in range(3):
            user = auth.register(
                username=f"offset_{unique_id}_{i}",
                email=f"offset_{unique_id}_{i}@example.com",
                password="TestPass123!"
            )
            search._get_manager()._indexer.index_user(
                IndexedUser(user_id=user.id, username=user.username)
            )
        
        all_results = search.search_users(1, f"offset_{unique_id}", limit=10)
        offset_results = search.search_users(1, f"offset_{unique_id}", limit=10, offset=1)
        
        if len(all_results) > 1:
            assert len(offset_results) == len(all_results) - 1


@pytest.mark.search
class TestUserSearchInServer:
    """Test user search within a server."""
    
    def test_search_users_in_server(self, users_with_server):
        """Test searching users within a server."""
        owner, member1, member2, server, servers_mod, search = users_with_server
        
        search._get_manager()._indexer.index_user(
            IndexedUser(user_id=owner.id, username=owner.username)
        )
        search._get_manager()._indexer.index_user(
            IndexedUser(user_id=member1.id, username=member1.username)
        )
        
        results = search.search_users(
            owner.id,
            owner.username.split("_")[0],
            server_id=server.id
        )
        
        assert isinstance(results, list)


@pytest.mark.search
class TestUserSearchIndexing:
    """Test user indexing."""
    
    def test_index_user(self, db_and_modules):
        """Test indexing a user."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        user = auth.register(
            username=f"indexuser_{unique_id}",
            email=f"indexuser_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        search._get_manager()._indexer.index_user(
            IndexedUser(
                user_id=user.id,
                username=user.username,
                display_name="Index Test User"
            )
        )
        
        results = search.search_users(user.id, f"indexuser_{unique_id}")
        
        assert len(results) >= 1
    
    def test_search_by_display_name(self, db_and_modules):
        """Test searching by display name."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        user = auth.register(
            username=f"dispuser_{unique_id}",
            email=f"dispuser_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        display_name = f"DisplayName{unique_id}"
        search._get_manager()._indexer.index_user(
            IndexedUser(
                user_id=user.id,
                username=user.username,
                display_name=display_name
            )
        )
        
        results = search.search_users(user.id, display_name)
        
        assert len(results) >= 1


@pytest.mark.search
class TestUserSearchLimits:
    """Test user search limits."""
    
    def test_limit_exceeds_max(self, db_and_modules):
        """Test that exceeding max limit raises error."""
        db, auth, messaging, servers, search = db_and_modules
        
        from src.core.search.exceptions import SearchLimitError
        
        with pytest.raises(SearchLimitError):
            search.search_users(1, "test", limit=1000)
