"""Tests for emoji routes."""


def test_get_server_emojis(test_client, test_server, test_user_with_token):
    """Test getting server emojis."""
    token = test_user_with_token["token"]
    server, user = test_server

    response = test_client.get(
        f"/api/v1/servers/{server.id}/emojis",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200


def test_get_server_emojis_without_auth(test_client, test_server):
    """Test that getting server emojis without authentication fails."""
    server, user = test_server
    response = test_client.get(f"/api/v1/servers/{server.id}/emojis")
    assert response.status_code == 401


def test_get_server_emojis_invalid_server_id(test_client, test_user_with_token):
    """Test that getting emojis with invalid server ID fails."""
    token = test_user_with_token["token"]
    response = test_client.get(
        "/api/v1/servers/invalid/emojis",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 400


class TestEmojiCacheInvalidation:
    """Tests for emoji cache invalidation helpers."""

    def test_invalidate_emoji_cache_clears_server_list_only(self, monkeypatch):
        """List cache invalidation runs without an emoji ID."""
        from src.api.routes.emojis import _invalidate_emoji_cache

        invalidated = []
        monkeypatch.setattr(
            "src.api.routes.emojis.invalidate_pattern", invalidated.append
        )

        _invalidate_emoji_cache(server_id=123, emoji_id=None)

        # Exactly one invalidation: the server list pattern.
        assert len(invalidated) == 1
        assert ":str:123:*" in invalidated[0]

    def test_invalidate_emoji_cache_clears_server_list_and_detail(self, monkeypatch):
        """List and detail cache invalidation with an emoji ID."""
        from src.api.routes.emojis import _invalidate_emoji_cache

        invalidated = []
        monkeypatch.setattr(
            "src.api.routes.emojis.invalidate_pattern", invalidated.append
        )

        _invalidate_emoji_cache(server_id=123, emoji_id=456)

        # One invalidation for the server list plus one for the detail.
        assert len(invalidated) == 2
        assert any(":str:123:" in pattern for pattern in invalidated)
        assert any(":str:456:" in pattern for pattern in invalidated)
