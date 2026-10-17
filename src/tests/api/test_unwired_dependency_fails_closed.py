"""API-layer fail-closed behavior when an optional dependency is unwired.

Companion to ``src/tests/security/test_unwired_dependency_fails_closed.py``,
which covers the ``src.core`` managers. This module covers the **HTTP layer**:
several routes guarded an authorization step with ``if servers_module:``, so
when the servers module was not wired the check was *skipped* and every
authenticated caller could read or mutate any server's resources.

The invariant:

    A check that cannot be evaluated because its dependency is unwired must
    DENY, not skip.

``servers_module`` is simulated as unwired by patching ``api.get_servers`` to
return ``None`` (that getter has a module-singleton fallback, so returning
``None`` is the only faithful way to express "not wired" at the API boundary).
The rest of the module graph is the real production wiring from the shared
``test_client`` fixture, so each request exercises the real route, real auth
middleware, and real database.
"""

import pytest
from types import SimpleNamespace


EMOJI_LIST_URL = "/api/v1/servers/{server_id}/emojis"
EMOJI_COUNTS_URL = "/api/v1/servers/{server_id}/emojis/counts"
EMOJI_DETAIL_URL = "/api/v1/servers/{server_id}/emojis/{emoji_id}"
SLOWMODE_URL = "/api/v1/features/channels/{channel_id}/slowmode"

# Status used by the emoji routes for "dependency unavailable" (matches the
# soundboard W5 route gate); the feature/slowmode module uses its local
# ``raise_internal`` convention instead.
UNAVAILABLE = 503


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def unwired_servers(monkeypatch):
    """Make ``api.get_servers()`` report the servers module as unwired."""
    import src.api as api

    monkeypatch.setattr(api, "get_servers", lambda: None)
    return None


# =============================================================================
# Emoji routes
# =============================================================================


class TestEmojiRoutesFailClosedWithoutServers:
    """Every emoji route delegates authorization to the servers module."""

    def test_list_server_emojis_denied(
        self, test_client, test_server, test_user_with_token, unwired_servers
    ):
        server, _ = test_server
        resp = test_client.get(
            EMOJI_LIST_URL.format(server_id=server.id),
            headers=_headers(test_user_with_token["token"]),
        )
        assert resp.status_code == UNAVAILABLE, resp.text

    def test_emoji_counts_denied(
        self, test_client, test_server, test_user_with_token, unwired_servers
    ):
        server, _ = test_server
        resp = test_client.get(
            EMOJI_COUNTS_URL.format(server_id=server.id),
            headers=_headers(test_user_with_token["token"]),
        )
        assert resp.status_code == UNAVAILABLE, resp.text

    def test_emoji_detail_denied(
        self,
        test_client,
        test_server,
        test_user_with_token,
        monkeypatch,
        unwired_servers,
    ):
        import src.api as api

        server, _ = test_server
        emoji = SimpleNamespace(
            id=424242,
            server_id=int(server.id),
            name="wave",
            animated=False,
            url="https://cdn.example.com/wave.png",
            available=True,
            created_by=1,
            uploader_username="owner",
            created_at=0,
        )
        monkeypatch.setattr(
            api,
            "get_reactions",
            lambda: SimpleNamespace(get_custom_emoji=lambda eid: emoji),
        )

        resp = test_client.get(
            EMOJI_DETAIL_URL.format(server_id=server.id, emoji_id=emoji.id),
            headers=_headers(test_user_with_token["token"]),
        )
        # The membership gate now runs (it previously did not exist at all);
        # an unwired servers module must deny rather than return the emoji.
        assert resp.status_code == UNAVAILABLE, resp.text

    def test_create_emoji_denied(
        self, test_client, test_server, test_user_with_token, unwired_servers
    ):
        server, _ = test_server
        resp = test_client.post(
            EMOJI_LIST_URL.format(server_id=server.id),
            data={"name": "wave"},
            files={"image": ("wave.png", b"\x89PNG\r\n\x1a\n", "image/png")},
            headers=_headers(test_user_with_token["token"]),
        )
        assert resp.status_code == UNAVAILABLE, resp.text

    def test_update_emoji_denied(
        self, test_client, test_server, test_user_with_token, unwired_servers
    ):
        server, _ = test_server
        resp = test_client.patch(
            EMOJI_DETAIL_URL.format(server_id=server.id, emoji_id=424242),
            json={"name": "renamed"},
            headers=_headers(test_user_with_token["token"]),
        )
        assert resp.status_code == UNAVAILABLE, resp.text

    def test_delete_emoji_denied(
        self, test_client, test_server, test_user_with_token, unwired_servers
    ):
        server, _ = test_server
        resp = test_client.delete(
            EMOJI_DETAIL_URL.format(server_id=server.id, emoji_id=424242),
            headers=_headers(test_user_with_token["token"]),
        )
        assert resp.status_code == UNAVAILABLE, resp.text


class TestEmojiRoutesStillGatedWhenWired:
    """With the real module graph the gate must be enforced, not blanket-503."""

    def test_non_member_listing_is_forbidden(
        self, test_client, test_server, second_test_user
    ):
        server, _ = test_server
        resp = test_client.get(
            EMOJI_LIST_URL.format(server_id=server.id),
            headers=_headers(second_test_user["token"]),
        )
        assert resp.status_code == 403, resp.text
        assert "Not a member" in resp.text

    def test_owner_listing_succeeds(
        self, test_client, test_server, test_user_with_token
    ):
        server, _ = test_server
        resp = test_client.get(
            EMOJI_LIST_URL.format(server_id=server.id),
            headers=_headers(test_user_with_token["token"]),
        )
        assert resp.status_code == 200, resp.text

    def test_non_member_create_is_forbidden(
        self, test_client, test_server, second_test_user
    ):
        server, _ = test_server
        resp = test_client.post(
            EMOJI_LIST_URL.format(server_id=server.id),
            data={"name": "wave"},
            files={"image": ("wave.png", b"\x89PNG\r\n\x1a\n", "image/png")},
            headers=_headers(second_test_user["token"]),
        )
        assert resp.status_code == 403, resp.text


# =============================================================================
# Channel slowmode read gate
# =============================================================================


class TestChannelSlowmodeReadGateFailsClosed:
    def test_read_denied_without_servers(
        self,
        test_client,
        test_server,
        test_user_with_token,
        server_manager,
        unwired_servers,
    ):
        server, owner = test_server
        channel = server_manager.get_channels(owner.id, server.id)[0]

        resp = test_client.get(
            SLOWMODE_URL.format(channel_id=channel.id),
            headers=_headers(test_user_with_token["token"]),
        )
        # Previously an unwired servers module skipped the view gate entirely
        # and returned the channel's slowmode (plus channel existence) to any
        # authenticated caller. The route now fails closed via its local
        # ``raise_internal`` convention (500), whose body the error middleware
        # redacts - only the status is asserted here.
        assert resp.status_code == 500, resp.text

    def test_owner_read_succeeds_when_wired(
        self, test_client, test_server, test_user_with_token, server_manager
    ):
        server, owner = test_server
        channel = server_manager.get_channels(owner.id, server.id)[0]

        resp = test_client.get(
            SLOWMODE_URL.format(channel_id=channel.id),
            headers=_headers(test_user_with_token["token"]),
        )
        assert resp.status_code == 200, resp.text
