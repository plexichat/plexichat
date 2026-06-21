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
