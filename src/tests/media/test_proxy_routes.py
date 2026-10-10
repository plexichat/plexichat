"""API tests for the external URL proxy route and message-embed rejection."""

import uuid
from unittest.mock import Mock, patch

from src.utils import encryption

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24


def _register_and_login(auth_manager, label="user"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@example.com"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return result.token, user


class TestProxyRoute:
    """GET /api/v1/media/proxy behavior."""

    def test_requires_auth(self, test_client):
        resp = test_client.get(
            "/api/v1/media/proxy?url=https%3A%2F%2Fexample.com%2Fa.png"
        )
        assert resp.status_code == 401

    def test_rejects_unsafe_scheme(self, test_client, auth_manager):
        token, _ = _register_and_login(auth_manager)
        resp = test_client.get(
            "/api/v1/media/proxy?url=javascript%3Aalert(1)",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400

    def test_rejects_missing_url(self, test_client, auth_manager):
        token, _ = _register_and_login(auth_manager)
        resp = test_client.get(
            "/api/v1/media/proxy",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400

    def test_serves_proxied_content(self, test_client, auth_manager):
        token, _ = _register_and_login(auth_manager)

        media_mod = Mock()
        media_mod.get_proxied_content = Mock(return_value=(_PNG, "image/png"))

        with patch("src.api.routes.media.api.get_media", return_value=media_mod):
            resp = test_client.get(
                "/api/v1/media/proxy?url=https%3A%2F%2Fexample.com%2Fa.png",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content == _PNG
        media_mod.get_proxied_content.assert_called_once_with(
            "https://example.com/a.png"
        )

    def test_maps_proxy_error_to_400(self, test_client, auth_manager):
        from src.core.media.exceptions import ProxyFetchError

        token, _ = _register_and_login(auth_manager)

        media_mod = Mock()
        media_mod.get_proxied_content = Mock(
            side_effect=ProxyFetchError(
                "SVG content is blocked", "https://x.test/a.png"
            )
        )

        with patch("src.api.routes.media.api.get_media", return_value=media_mod):
            resp = test_client.get(
                "/api/v1/media/proxy?url=https%3A%2F%2Fexample.com%2Fa.png",
                headers={"Authorization": f"Bearer {token}"},
            )

        assert resp.status_code == 400


class TestSendMessageEmbedRejection:
    """POST /api/v1/channels/{channel_id}/messages embed validation."""

    def test_send_message_rejects_malicious_embed(
        self, test_client, auth_manager, messaging_manager
    ):
        token, sender = _register_and_login(auth_manager, "sender")
        _, recipient = _register_and_login(auth_manager, "recipient")

        dm = messaging_manager.create_dm(sender.id, recipient.id)

        resp = test_client.post(
            f"/api/v1/channels/{dm.id}/messages",
            json={
                "content": "hello",
                "embeds": [
                    {
                        "type": "server_invite",
                        "title": "x",
                        "fields": [
                            {"name": "Members", "value": "<img src=x onerror=alert(1)>"}
                        ],
                    }
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 400

    def test_send_message_sanitizes_embed(
        self, test_client, auth_manager, messaging_manager
    ):
        token, sender = _register_and_login(auth_manager, "sender2")
        _, recipient = _register_and_login(auth_manager, "recipient2")

        dm = messaging_manager.create_dm(sender.id, recipient.id)

        resp = test_client.post(
            f"/api/v1/channels/{dm.id}/messages",
            json={
                "content": "hello",
                "embeds": [
                    {
                        "type": "server_invite",
                        "title": "Join",
                        "color": 0x5865F2,
                        "fields": [{"name": "Members", "value": "42"}],
                    }
                ],
            },
            headers={"Authorization": f"Bearer {token}"},
        )

        assert resp.status_code == 200
        embeds = resp.json().get("embeds") or []
        assert len(embeds) == 1
        assert embeds[0]["color"] == "#5865F2"
