"""
Tests for server search functionality.
"""

import pytest
import uuid

from src.core.search.models import ServerSearchResult, IndexedServer


@pytest.mark.search
class TestServerSearchBasic:
    """Test basic server search."""
    
    def test_search_servers_returns_list(self, db_and_modules):
        """Test that server search returns a list."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"srvowner_{unique_id}",
            email=f"srvowner_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"Searchable Server {unique_id}")
        
        search._get_manager()._indexer.index_server(
            IndexedServer(
                server_id=server.id,
                name=server.name,
                is_public=True
            )
        )
        
        results = search.search_servers(owner.id, unique_id)
        
        assert isinstance(results, list)
    
    def test_search_servers_empty_query(self, db_and_modules):
        """Test search with empty query."""
        db, auth, messaging, servers, search = db_and_modules
        
        results = search.search_servers(1, "")
        
        assert results == []
    
    def test_search_servers_result_type(self, db_and_modules):
        """Test search results are ServerSearchResult."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"srvtype_{unique_id}",
            email=f"srvtype_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"Type Check Server {unique_id}")
        
        search._get_manager()._indexer.index_server(
            IndexedServer(
                server_id=server.id,
                name=server.name,
                is_public=True
            )
        )
        
        results = search.search_servers(owner.id, unique_id)
        
        if results:
            assert isinstance(results[0], ServerSearchResult)


@pytest.mark.search
class TestServerSearchFilters:
    """Test server search with filters."""
    
    def test_search_servers_with_limit(self, db_and_modules):
        """Test search respects limit."""
        db, auth, messaging, servers, search = db_and_modules
        
        results = search.search_servers(1, "server", limit=5)
        
        assert len(results) <= 5
    
    def test_search_servers_by_category(self, db_and_modules):
        """Test search by category."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"catowner_{unique_id}",
            email=f"catowner_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"Gaming Server {unique_id}")
        
        search._get_manager()._indexer.index_server(
            IndexedServer(
                server_id=server.id,
                name=server.name,
                category="gaming",
                is_public=True
            )
        )
        
        results = search.search_servers(owner.id, unique_id, category="gaming")
        
        for result in results:
            if result.category:
                assert result.category == "gaming"


@pytest.mark.search
class TestServerSearchIndexing:
    """Test server indexing."""
    
    def test_index_server(self, db_and_modules):
        """Test indexing a server."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"idxowner_{unique_id}",
            email=f"idxowner_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"Index Test Server {unique_id}")
        
        search._get_manager()._indexer.index_server(
            IndexedServer(
                server_id=server.id,
                name=server.name,
                description="A test server for indexing",
                tags=["test", "indexing"],
                is_public=True
            )
        )
        
        results = search.search_servers(owner.id, unique_id)
        
        assert len(results) >= 1
    
    def test_search_by_description(self, db_and_modules):
        """Test searching by description."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"descowner_{unique_id}",
            email=f"descowner_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"Server {unique_id}")
        
        description = f"uniquedescription{unique_id}"
        search._get_manager()._indexer.index_server(
            IndexedServer(
                server_id=server.id,
                name=server.name,
                description=description,
                is_public=True
            )
        )
        
        results = search.search_servers(owner.id, description)
        
        assert len(results) >= 1
    
    def test_search_by_tags(self, db_and_modules):
        """Test searching by tags."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"tagowner_{unique_id}",
            email=f"tagowner_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        server = servers.create_server(owner.id, f"Tagged Server {unique_id}")
        
        tag = f"uniquetag{unique_id}"
        search._get_manager()._indexer.index_server(
            IndexedServer(
                server_id=server.id,
                name=server.name,
                tags=[tag, "gaming"],
                is_public=True
            )
        )
        
        results = search.search_servers(owner.id, tag)
        
        assert len(results) >= 1


@pytest.mark.search
class TestServerSearchLimits:
    """Test server search limits."""
    
    def test_limit_exceeds_max(self, db_and_modules):
        """Test that exceeding max limit raises error."""
        db, auth, messaging, servers, search = db_and_modules
        
        from src.core.search.exceptions import SearchLimitError
        
        with pytest.raises(SearchLimitError):
            search.search_servers(1, "test", limit=1000)


@pytest.mark.search
class TestServerSearchPublicOnly:
    """Test that server search only returns public servers."""
    
    def test_only_public_servers_returned(self, db_and_modules):
        """Test that only public servers are returned."""
        db, auth, messaging, servers, search = db_and_modules
        
        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"pubowner_{unique_id}",
            email=f"pubowner_{unique_id}@example.com",
            password="TestPass123!"
        )
        
        public_server = servers.create_server(owner.id, f"Public {unique_id}")
        private_server = servers.create_server(owner.id, f"Private {unique_id}")
        
        search._get_manager()._indexer.index_server(
            IndexedServer(
                server_id=public_server.id,
                name=public_server.name,
                is_public=True
            )
        )
        search._get_manager()._indexer.index_server(
            IndexedServer(
                server_id=private_server.id,
                name=private_server.name,
                is_public=False
            )
        )
        
        results = search.search_servers(owner.id, unique_id)
        
        for result in results:
            indexed = search._get_manager()._indexer._db.fetch_one(
                "SELECT is_public FROM search_servers_fts WHERE server_id = ?",
                (str(result.server_id),)
            )
            if indexed:
                assert indexed["is_public"] == "1"
