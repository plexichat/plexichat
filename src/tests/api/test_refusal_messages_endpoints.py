"""HTTP pins for the canonical 4xx refusal messages.

Routes map specific failures to a 4xx status; the message must name the
refusal (the canonical per-status text in
``src/api/middleware/error_handling._FALLBACK_MESSAGE_BY_STATUS``), not the
generic 500 text. These drive the real routes so the sweep that aligned them
is covered behaviourally, complementing the source ratchet in
``test_refusal_messages``.
"""

import struct
import uuid
import zlib


def _png() -> bytes:
    """Return a minimal valid 1x1 PNG accepted by the emoji validators."""
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    ihdr_crc = zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", ihdr_crc)
    raw = b"\x00\x00\x00\x00\x00"
    compressed = zlib.compress(raw)
    idat_crc = zlib.crc32(b"IDAT" + compressed) & 0xFFFFFFFF
    idat = (
        struct.pack(">I", len(compressed))
        + b"IDAT"
        + compressed
        + struct.pack(">I", idat_crc)
    )
    iend_crc = zlib.crc32(b"IEND") & 0xFFFFFFFF
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", iend_crc)
    return sig + ihdr + idat + iend


def _message(response) -> str:
    return response.json()["error"]["message"]


class TestPollRefusals:
    def test_unknown_poll_reports_resource_not_found(self, test_client, auth_headers):
        # A syntactically valid but absent id (within the int64 snowflake range).
        response = test_client.get(
            "/api/v1/polls/123456789012345678", headers=auth_headers
        )
        assert response.status_code == 404, response.text
        assert _message(response) == "Resource not found"

    def test_non_numeric_poll_id_reports_invalid_poll_id(
        self, test_client, auth_headers
    ):
        response = test_client.get("/api/v1/polls/not-a-number", headers=auth_headers)
        assert response.status_code == 400, response.text
        assert _message(response) == "Invalid poll ID"


class TestEmojiRefusals:
    def test_invalid_server_id_reports_invalid_server_id(
        self, test_client, auth_headers
    ):
        response = test_client.get(
            "/api/v1/servers/invalid/emojis", headers=auth_headers
        )
        assert response.status_code == 400, response.text
        assert _message(response) == "Invalid server ID"

    def test_invalid_emoji_name_reports_invalid_request(
        self, test_client, auth_headers, test_user_with_token, modules
    ):
        owner = test_user_with_token["user"]
        server = modules.servers.create_server(
            owner.id, f"Emojis {uuid.uuid4().hex[:6]}"
        )
        # The shared test ReactionManager is built without a servers module, so
        # its fail-closed manage gate denies every write; wire it up as the
        # read-surface sweep does.
        modules.reactions._servers = modules.servers

        response = test_client.post(
            f"/api/v1/servers/{server.id}/emojis",
            headers=auth_headers,
            data={"name": "!!"},
            files={"image": ("e.png", _png(), "image/png")},
        )
        assert response.status_code == 400, response.text
        assert _message(response) == "Invalid request"


class TestServerRefusals:
    def test_invalid_server_id_reports_invalid_server_id(
        self, test_client, auth_headers
    ):
        response = test_client.get(
            "/api/v1/servers/invalid/roles", headers=auth_headers
        )
        assert response.status_code == 400, response.text
        assert _message(response) == "Invalid server ID"

    def test_unknown_permission_key_reports_invalid_request(
        self, test_client, auth_headers, test_user_with_token, modules
    ):
        owner = test_user_with_token["user"]
        server = modules.servers.create_server(
            owner.id, f"Roles {uuid.uuid4().hex[:6]}"
        )

        # An unknown permission key is rejected before any grant check, so the
        # client gets the actionable 400 rather than a permission 403.
        response = test_client.post(
            f"/api/v1/servers/{server.id}/roles",
            headers=auth_headers,
            json={
                "name": "Refusal Test Role",
                "permissions": {"not.a.real.permission": True},
            },
        )
        assert response.status_code == 400, response.text
        assert _message(response) == "Invalid request"

    def test_unknown_permission_key_on_update_reports_invalid_request(
        self, test_client, auth_headers, test_user_with_token, modules
    ):
        owner = test_user_with_token["user"]
        server = modules.servers.create_server(
            owner.id, f"Roles {uuid.uuid4().hex[:6]}"
        )
        role = modules.servers.create_role(owner.id, server.id, "Refusal Update Role")

        # The same 400-before-403 ordering must hold on PATCH: an unknown
        # permission key is client input error, not a permission refusal.
        response = test_client.patch(
            f"/api/v1/servers/{server.id}/roles/{role.id}",
            headers=auth_headers,
            json={"permissions": {"not.a.real.permission": True}},
        )
        assert response.status_code == 400, response.text
        assert _message(response) == "Invalid request"
