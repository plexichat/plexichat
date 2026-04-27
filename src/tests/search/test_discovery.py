"""
Tests for server discovery functionality.
"""

import pytest
import uuid

from src.core.search.models import ServerListing, ServerCategory, VerificationLevel
from src.core.search.exceptions import (
    ServerNotListedError,
    CategoryNotFoundError,
)


class TestServerCategories:
    """Test server category functionality."""

    def test_get_categories(self, db_and_search):
        """Test getting all categories."""
        db, auth, messaging, servers, search = db_and_search

        categories = search.get_server_categories()

        assert isinstance(categories, list)
        assert len(categories) > 0

    def test_category_has_required_fields(self, db_and_search):
        """Test categories have required fields."""
        db, auth, messaging, servers, search = db_and_search

        categories = search.get_server_categories()

        for cat in categories:
            assert isinstance(cat, ServerCategory)
            assert cat.id is not None
            assert cat.name is not None

    def test_default_categories_exist(self, db_and_search):
        """Test default categories are seeded."""
        db, auth, messaging, servers, search = db_and_search

        categories = search.get_server_categories()
        category_ids = [c.id for c in categories]

        assert "gaming" in category_ids
        assert "music" in category_ids
        assert "social" in category_ids


@pytest.mark.search
class TestServerListing:
    """Test server listing functionality."""

    def test_list_server(self, users_with_server_search):
        """Test listing a server."""
        owner, members, server, servers_mod, search = users_with_server_search

        listing = search.list_server(
            user_id=owner.id,
            server_id=server.id,
            category="gaming",
            description="A test gaming server",
            tags=["test", "gaming"],
        )

        assert isinstance(listing, ServerListing)
        assert listing.server_id == server.id
        assert listing.category == "gaming"

    def test_list_server_invalid_category(self, users_with_server_search):
        """Test listing with invalid category raises error."""
        owner, members, server, servers_mod, search = users_with_server_search

        with pytest.raises(CategoryNotFoundError):
            search.list_server(
                user_id=owner.id, server_id=server.id, category="invalid_category_xyz"
            )

    def test_list_server_updates_existing(self, users_with_server_search):
        """Test listing an already listed server updates it."""
        owner, members, server, servers_mod, search = users_with_server_search

        search.list_server(
            user_id=owner.id,
            server_id=server.id,
            category="gaming",
            description="Original description",
        )

        listing2 = search.list_server(
            user_id=owner.id,
            server_id=server.id,
            category="music",
            description="Updated description",
        )

        assert listing2.category == "music"

    def test_unlist_server(self, users_with_server_search):
        """Test unlisting a server."""
        owner, members, server, servers_mod, search = users_with_server_search

        search.list_server(user_id=owner.id, server_id=server.id, category="gaming")

        result = search.unlist_server(owner.id, server.id)

        assert result is True

    def test_unlist_server_not_listed(self, db_and_search):
        """Test unlisting a server that's not listed."""
        db, auth, messaging, servers, search = db_and_search

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"unlist_owner_{unique_id}",
            email=f"unlist_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Unlist Test {unique_id}")

        with pytest.raises(ServerNotListedError):
            search.unlist_server(owner.id, server.id)


@pytest.mark.search
class TestPublicServerListing:
    """Test public server listing."""

    def test_list_public_servers(self, users_with_server_search):
        """Test listing public servers."""
        owner, members, server, servers_mod, search = users_with_server_search

        search.list_server(user_id=owner.id, server_id=server.id, category="gaming")

        listings = search.list_public_servers()

        assert isinstance(listings, list)

    def test_list_public_servers_by_category(self, users_with_server_search):
        """Test listing public servers by category."""
        owner, members, server, servers_mod, search = users_with_server_search

        search.list_server(user_id=owner.id, server_id=server.id, category="gaming")

        listings = search.list_public_servers(category="gaming")

        for listing in listings:
            assert listing.category == "gaming"

    def test_list_public_servers_sort_by_members(self, users_with_server_search):
        """Test sorting by member count."""
        owner, members, server, servers_mod, search = users_with_server_search

        search.list_server(user_id=owner.id, server_id=server.id, category="gaming")

        listings = search.list_public_servers(sort_by="member_count")

        if len(listings) > 1:
            for i in range(len(listings) - 1):
                assert listings[i].member_count >= listings[i + 1].member_count

    def test_list_public_servers_limit(self, users_with_server_search):
        """Test listing with limit."""
        owner, members, server, servers_mod, search = users_with_server_search

        listings = search.list_public_servers(limit=5)

        assert len(listings) <= 5


@pytest.mark.search
class TestServerBumping:
    """Test server bumping functionality."""

    @pytest.mark.skip(
        "Bump cooldown (4 hours) makes this test difficult to run in isolation"
    )
    def test_bump_server(self, db_and_search):
        """Test bumping a server."""
        db, auth, messaging, servers, search = db_and_search

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"bump_owner_{unique_id}",
            email=f"bump_owner_{unique_id}@example.com",
            password="TestPass123!",
        )
        server = servers.create_server(owner.id, f"Bump Test {unique_id}")

        # Add 10 members to meet minimum requirement
        for i in range(10):
            member = auth.register(
                username=f"bump_member_{unique_id}_{i}",
                email=f"bump_member_{unique_id}_{i}@example.com",
                password="TestPass123!",
            )
            servers.add_member(server.id, member.id)

        search.list_server(user_id=owner.id, server_id=server.id, category="gaming")

        result = search.bump_server(owner.id, server.id)

        assert result is True

    def test_bump_server_not_listed(self, db_and_search):
        """Test bumping a server that's not listed."""
        db, auth, messaging, servers, search = db_and_search

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"bump_owner_{unique_id}",
            email=f"bump_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Bump Test {unique_id}")

        with pytest.raises(ServerNotListedError):
            search.bump_server(owner.id, server.id)


@pytest.mark.search
class TestServerVerification:
    """Test server verification functionality."""

    def test_verify_server(self, users_with_server_search):
        """Test verifying a server."""
        owner, members, server, servers_mod, search = users_with_server_search

        search.list_server(user_id=owner.id, server_id=server.id, category="gaming")

        result = search.verify_server(server.id, VerificationLevel.LOW)

        assert result is True

    def test_verify_server_not_listed(self, db_and_search):
        """Test verifying a server that's not listed."""
        db, auth, messaging, servers, search = db_and_search

        unique_id = uuid.uuid4().hex[:8]
        owner = auth.register(
            username=f"verify_owner_{unique_id}",
            email=f"verify_owner_{unique_id}@example.com",
            password="TestPass123!",
        )

        server = servers.create_server(owner.id, f"Verify Test {unique_id}")

        with pytest.raises(ServerNotListedError):
            search.verify_server(server.id, VerificationLevel.HIGH)


@pytest.mark.search
class TestServerSearch:
    """Test server search functionality."""

    def test_search_servers(self, users_with_server_search):
        """Test searching servers."""
        owner, members, server, servers_mod, search = users_with_server_search

        unique_id = uuid.uuid4().hex[:8]

        search._get_manager()._indexer.index_server(
            search.models.IndexedServer(
                server_id=server.id,
                name=f"Searchable {unique_id}",
                description="A searchable server",
                is_public=True,
            )
        )

        results = search.search_servers(owner.id, unique_id)

        assert isinstance(results, list)

    def test_search_servers_by_category(self, users_with_server_search):
        """Test searching servers by category."""
        owner, members, server, servers_mod, search = users_with_server_search

        unique_id = uuid.uuid4().hex[:8]

        search._get_manager()._indexer.index_server(
            search.models.IndexedServer(
                server_id=server.id,
                name=f"Gaming {unique_id}",
                category="gaming",
                is_public=True,
            )
        )

        results = search.search_servers(owner.id, unique_id, category="gaming")

        for result in results:
            if result.category:
                assert result.category == "gaming"
