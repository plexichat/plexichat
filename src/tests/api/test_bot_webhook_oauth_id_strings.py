"""HTTP sweep: bot, webhook and OAuth responses carry string snowflake ids.

These surfaces were not covered by the earlier auth/artifact/search sweeps.
Each response is scanned recursively for any integer at or above 2**53 and for
any ``id`` / ``*_id`` field that came back as a number. ``BotRequestResponse.id``
was typed ``int`` and leaked a raw id until this test forced the fix.
"""

import uuid
from typing import Any

MIN_SNOWFLAKE = 2**53

_ID_KEYS = (
    "id",
    "user_id",
    "server_id",
    "channel_id",
    "application_id",
    "requester_id",
    "creator_id",
    "bot_id",
)


def _scan(node: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            if isinstance(value, bool):
                continue
            if isinstance(value, int) and abs(value) >= MIN_SNOWFLAKE:
                found.append(f"{child} = {value} (numeric snowflake)")
            elif key in _ID_KEYS and isinstance(value, int):
                found.append(f"{child} = {value} (id is not a string)")
            found += _scan(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found += _scan(value, f"{path}[{index}]")
    return found


def test_scanner_flags_numeric_ids():
    assert _scan({"id": 9007199254740993}) != []
    assert _scan({"id": "9007199254740993"}) == []
    assert _scan({"application_id": 42}) != []
    assert _scan({"total": 42}) == []
    assert _scan({"bots": [{"id": 10**18}]}) != []


def _check(r, where: str, status=(200,)) -> Any:
    assert r.status_code in status, f"{where} -> {r.status_code}: {r.text}"
    body = r.json()
    hits = _scan(body)
    assert not hits, (
        f"{where} returned a numeric snowflake id; serialize it as a decimal "
        "string instead:\n" + "\n".join(hits)
    )
    return body


def _mint_authorized_token(app_manager, user, app):
    """Create a valid OAuth access token for ``app`` so it lists as authorized."""
    from src.core.applications.oauth.flows import OAuth2Flow
    from src.core.applications.oauth.tokens import generate_client_secret

    flow = OAuth2Flow(
        app_manager._db,
        {
            "code_expiry_seconds": 600,
            "token_expiry_seconds": 604800,
            "refresh_enabled": True,
        },
    )
    code = flow.create_authorization_code(
        application_id=app.id,
        user_id=user.id,
        redirect_uri="https://example.com/callback",
        scopes=["identify"],
    )
    client_secret, secret_hash = generate_client_secret()
    app_manager._db.execute(
        "UPDATE app_applications SET client_secret_hash = ? WHERE id = ?",
        (secret_hash, app.id),
    )
    return flow.exchange_code(
        app.id, client_secret, code.code, "https://example.com/callback"
    )


class TestWebhookResponseIds:
    def test_webhook_read_returns_string_ids(
        self, test_client, auth_headers, modules, test_server
    ):
        server, user = test_server
        channel = modules.servers.get_channels(user.id, server.id)[0]
        webhook = modules.webhooks.create_webhook(
            user_id=user.id,
            channel_id=channel.id,
            name=f"Sweep {uuid.uuid4().hex[:6]}",
        )

        body = _check(
            test_client.get(f"/api/v1/webhooks/{webhook.id}", headers=auth_headers),
            "GET /webhooks/{id}",
        )
        assert body["id"] == str(webhook.id)
        assert body["channel_id"] == str(channel.id)
        assert body["server_id"] == str(server.id)


class TestBotAndOAuthResponseIds:
    def test_authorized_apps_return_string_ids(
        self, test_client, auth_headers, test_user_with_token
    ):
        from src.core import applications

        owner = test_user_with_token["user"]
        app = applications.create_application(
            owner_id=owner.id,
            name=f"OAuth {uuid.uuid4().hex[:6]}",
            redirect_uris=["https://example.com/callback"],
        )
        _mint_authorized_token(applications._manager, owner, app)

        body = _check(
            test_client.get("/api/v1/bots/authorized-apps", headers=auth_headers),
            "GET /bots/authorized-apps",
        )
        assert body, "authorized app list was empty"
        assert body[0]["application_id"] == str(app.id)

    def test_bot_profile_returns_string_ids(
        self, test_client, auth_headers, test_user_with_token
    ):
        from src.core import applications

        owner = test_user_with_token["user"]
        app = applications.create_application(
            owner_id=owner.id,
            name=f"Profile {uuid.uuid4().hex[:6]}",
            redirect_uris=["https://example.com/callback"],
        )
        applications.create_bot_for_application(user_id=owner.id, application_id=app.id)
        applications.update_bot_profile(
            application_id=app.id, user_id=owner.id, private=False
        )

        body = _check(
            test_client.get(f"/api/v1/bots/profiles/{app.id}", headers=auth_headers),
            "GET /bots/profiles/{id}",
        )
        assert body["application_id"] == str(app.id)

    def test_bot_request_returns_string_id(
        self, test_client, auth_headers, test_user_with_token, modules
    ):
        from src.core import applications

        owner = test_user_with_token["user"]
        app = applications.create_application(
            owner_id=owner.id,
            name=f"Request {uuid.uuid4().hex[:6]}",
            redirect_uris=["https://example.com/callback"],
        )
        applications.create_bot_for_application(user_id=owner.id, application_id=app.id)
        server = modules.servers.create_server(owner.id, f"Bots {uuid.uuid4().hex[:6]}")

        body = _check(
            test_client.post(
                f"/api/v1/bots/servers/{server.id}/request",
                headers=auth_headers,
                json={"application_id": str(app.id)},
            ),
            "POST /bots/servers/{id}/request",
            status=(201,),
        )
        assert isinstance(body["id"], str)
        assert body["server_id"] == str(server.id)

        listed = _check(
            test_client.get(
                f"/api/v1/bots/servers/{server.id}/requests", headers=auth_headers
            ),
            "GET /bots/servers/{id}/requests",
        )
        for request in listed:
            assert isinstance(request["id"], str)
