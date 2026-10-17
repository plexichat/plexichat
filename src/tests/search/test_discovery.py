"""Search - server discovery (public listings + bump + verify)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def seeded(search_manager, server_manager, auth_manager, pii_gen):
    from unittest.mock import patch
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        for i in range(12):
            owner = auth_manager.register(
                username=f"discoveryuser{i}",
                email=pii_gen.email(),
                password="TestPass123!",
            )
            server_manager.create_server(
                owner_id=owner.id,
                name=f"Public Server {i}",
                is_public=True,
            )
    return search_manager, server_manager, auth_manager


class TestServerDiscovery:
    def test_list_public_servers(self, seeded):
        sm, _sm, _am = seeded
        listings = sm.list_public_servers(limit=10)
        assert isinstance(listings, list)

    def test_get_server_categories(self, seeded):
        sm, *_ = seeded
        cats = sm.get_server_categories()
        assert isinstance(cats, list)

    def test_bump_server_via_manager(self, seeded):
        sm, srv_mgr, _am = seeded
        from src.core.search.models import VerificationLevel

        res = sm.bump_server(user_id=1, server_id=999999999)
        # Doesn't matter if the server doesn't exist — the API
        # contract is "always returns a bool".
        assert isinstance(res, bool)
        # Verify-level mutation similarly:
        assert isinstance(sm.verify_server(1, VerificationLevel.NONE), bool)


class TestDiscoveryRouteSnowflakeIds:
    """HTTP contract: a discovery listing's snowflake ids cross as strings.

    The route serializes ``ServerListing`` dataclasses, whose ``id`` and
    ``server_id`` are 64-bit ints. Served as JSON numbers a JS client rounds
    them above 2^53; they must be decimal strings like every other schema.
    """

    def test_route_stringifies_listing_ids(
        self, test_client, db, server_manager, search_manager, logged_in_user
    ):
        # Constructing ``search_manager`` creates the discovery tables.
        user, token, _auth, _username = logged_in_user
        server = server_manager.create_server(
            owner_id=user.id, name="Route Discovery Server"
        )

        listing_id = 1234567890123456789
        db.execute(
            """INSERT INTO search_server_listings
               (id, server_id, category, description, tags, member_count,
                online_count, verification_level, is_verified, is_partnered,
                listed_at, bumped_at, bump_count, listed_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                listing_id,
                server.id,
                "gaming",
                "A seeded listing",
                "[]",
                1,
                0,
                "none",
                0,
                0,
                1700000000000,
                1700000000000,
                0,
                user.id,
            ),
        )

        response = test_client.get(
            "/api/v1/search/discovery/servers",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200, response.text
        results = response.json()["results"]
        entry = next(r for r in results if r["server_id"] == str(server.id))

        assert entry["id"] == str(listing_id)
        assert isinstance(entry["id"], str)
        assert entry["server_id"] == str(server.id)
        assert isinstance(entry["server_id"], str)
        # Non-id numbers stay numeric.
        assert entry["member_count"] == 1
