"""Regression: discovery listings must serialize snowflake ids as strings.

``GET /search/discovery/servers`` returns ``ServerListing`` dataclasses whose
``id`` and ``server_id`` are 64-bit snowflakes held as ints in the core layer.
Without the API-layer conversion FastAPI emits them as JSON numbers, and a
JavaScript ``Number`` rounds anything above 2^53, so the client would key the
listing by the wrong id. The route now stringifies them, matching the
SnowflakeID convention the rest of the search schemas already follow.
"""

import asyncio
from types import SimpleNamespace

import pytest

from src.core.search.models import ServerListing, VerificationLevel

pytestmark = pytest.mark.unit


class _FakeSearch:
    """Minimal search module exposing ``list_public_servers``."""

    def list_public_servers(
        self, category=None, sort_by="member_count", limit=25, offset=0
    ):
        return [
            ServerListing(
                id=1234567890123456789,
                server_id=9876543210987654321,
                name="Public Server",
                description="A listing",
                category="gaming",
                tags=["fun"],
                member_count=12,
                online_count=3,
                verification_level=VerificationLevel.NONE,
                listed_at=1700000000000,
                bumped_at=1700000000000,
                bump_count=0,
            )
        ]


def test_discovery_listing_ids_are_strings(monkeypatch):
    import src.api as api
    from src.api.routes.search import list_public_servers

    monkeypatch.setattr(api, "get_search", lambda: _FakeSearch())

    body = asyncio.run(
        list_public_servers(
            category=None,
            limit=25,
            offset=0,
            current_user=SimpleNamespace(user_id=1),
        )
    )

    listing = body["results"][0]
    assert listing["id"] == "1234567890123456789"
    assert isinstance(listing["id"], str)
    assert listing["server_id"] == "9876543210987654321"
    assert isinstance(listing["server_id"], str)
    # Non-id numbers must stay numeric.
    assert listing["member_count"] == 12
    assert listing["listed_at"] == 1700000000000
