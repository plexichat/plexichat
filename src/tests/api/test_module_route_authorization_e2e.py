"""Real-server e2e denial for the automod / webhooks / servers HTTP routes.

``test_client`` wires the production module graph (see
``_conftest/api_fixtures.py``), so these requests run through the real
``ServerManager`` membership store and the real ``require_permission`` path --
not a stubbed permission layer. A genuine non-member (authenticated, but not a
member of the server) must be refused with a 4xx.

That last point is the actual assertion: a refusal that surfaces as a 5xx reads
to clients and monitoring as a server fault, hides the authorization decision,
and is indistinguishable from an outage. Every test therefore asserts the
status is in the 4xx range *and* not 5xx, and each denial is paired with the
owner succeeding so the refusal is authorization-specific rather than a blanket
failure.
"""

from __future__ import annotations

import uuid

import pytest


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _denied(status: int) -> bool:
    """A non-member's refusal must be an access code, never 400 or 5xx.

    Each caller is a real, authenticated principal that simply is not a member
    of the server, so the request is well-formed and the resource exists. The
    honest answers are 403 (forbidden) or 404 (not found -- the servers routes
    deliberately conceal existence from non-members). Anything else, and
    especially a 400 or 500, hides the authorization decision behind what
    looks like a client or server fault.
    """
    return status in (403, 404)


@pytest.fixture
def wired_automod(db, modules):
    """Initialize the real automod module against this test's database.

    The API module graph the ``test_client`` wires does not include automod;
    without this the routes reach the module ``_get_manager()`` guard and every
    request fails as a server error. Mirrors ``server/initializer.py``.
    """
    from src.core import automod

    automod.setup(
        db,
        servers_module=modules.servers,
        messaging_module=modules.messaging,
        notifications_module=None,
    )
    try:
        yield modules
    finally:
        automod._manager = None
        automod._setup_complete = False


@pytest.fixture
def server_and_channel(modules, test_user_with_token):
    """A real server (owned by the token user) with one text channel."""
    owner = test_user_with_token["user"]
    server = modules.servers.create_server(
        owner.id, f"Authz E2E {uuid.uuid4().hex[:6]}"
    )
    channel = modules.servers.create_channel(
        owner.id, server.id, "general", channel_type=modules.servers.ChannelType.TEXT
    )
    return server, channel


# ---------------------------------------------------------------------------
# automod
# ---------------------------------------------------------------------------


def test_automod_rules_deny_a_non_member_with_403(
    test_client, server_and_channel, second_test_user
) -> None:
    """The non-member is genuinely authenticated, just not a member."""
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/automod/rules",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), (
        f"non-member got {response.status_code}: {response.text}"
    )


def test_automod_rules_allow_the_owner(
    test_client, wired_automod, server_and_channel, test_user_with_token
) -> None:
    """Complement: the same route succeeds for a caller who has server.automod."""
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/automod/rules",
        headers=_headers(test_user_with_token["token"]),
    )
    assert response.status_code == 200, response.text
    rules = response.json()
    assert isinstance(rules, list)
    # Every returned rule belongs to this server (servers are seeded with
    # default rules, so the list is not necessarily empty).
    assert all(rule["server_id"] == str(server.id) for rule in rules)


def test_automod_violations_deny_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/automod/violations",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), response.text


def test_automod_single_violation_denies_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/automod/violations/1",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), response.text


def test_automod_create_rule_denies_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    """A write must be refused for the same reason a read is."""
    server, _channel = server_and_channel
    response = test_client.post(
        f"/api/v1/servers/{server.id}/automod/rules",
        headers=_headers(second_test_user["token"]),
        json={
            "name": "block-bad-words",
            "rule_type": "keyword",
            "config": {"keywords": ["badword"]},
            "actions": [{"action_type": "delete_message"}],
        },
    )
    assert _denied(response.status_code), response.text


def test_automod_create_rule_allows_the_owner(
    test_client, wired_automod, server_and_channel, test_user_with_token
) -> None:
    server, _channel = server_and_channel
    response = test_client.post(
        f"/api/v1/servers/{server.id}/automod/rules",
        headers=_headers(test_user_with_token["token"]),
        json={
            "name": "block-bad-words",
            "rule_type": "keyword",
            "config": {"keywords": ["badword"]},
            "actions": [{"action_type": "delete_message"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["server_id"] == str(server.id)


def test_automod_delete_rule_denies_a_non_member(
    test_client, wired_automod, server_and_channel, second_test_user
) -> None:
    """Deletion is refused before the rule is even looked up."""
    server, _channel = server_and_channel
    response = test_client.delete(
        f"/api/v1/servers/{server.id}/automod/rules/1",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), response.text


def test_automod_update_rule_denies_a_non_member(
    test_client, wired_automod, server_and_channel, second_test_user
) -> None:
    server, _channel = server_and_channel
    response = test_client.patch(
        f"/api/v1/servers/{server.id}/automod/rules/1",
        headers=_headers(second_test_user["token"]),
        json={"name": "renamed"},
    )
    assert _denied(response.status_code), response.text


# ---------------------------------------------------------------------------
# webhooks
# ---------------------------------------------------------------------------


def test_server_webhooks_deny_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/webhooks",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), response.text


def test_server_webhooks_allow_the_owner(
    test_client, server_and_channel, test_user_with_token
) -> None:
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/webhooks",
        headers=_headers(test_user_with_token["token"]),
    )
    assert response.status_code == 200, response.text


def test_channel_webhooks_deny_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    _server, channel = server_and_channel
    response = test_client.get(
        f"/api/v1/channels/{channel.id}/webhooks",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), response.text


def test_channel_webhooks_allow_the_owner(
    test_client, server_and_channel, test_user_with_token
) -> None:
    _server, channel = server_and_channel
    response = test_client.get(
        f"/api/v1/channels/{channel.id}/webhooks",
        headers=_headers(test_user_with_token["token"]),
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# servers
# ---------------------------------------------------------------------------


def test_update_server_denies_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    server, _channel = server_and_channel
    response = test_client.patch(
        f"/api/v1/servers/{server.id}",
        headers=_headers(second_test_user["token"]),
        json={"name": "hijacked"},
    )
    assert _denied(response.status_code), response.text


def test_update_server_allows_the_owner(
    test_client, server_and_channel, test_user_with_token
) -> None:
    server, _channel = server_and_channel
    response = test_client.patch(
        f"/api/v1/servers/{server.id}",
        headers=_headers(test_user_with_token["token"]),
        json={"name": "renamed-by-owner"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["name"] == "renamed-by-owner"


# ---------------------------------------------------------------------------
# soundboard
# ---------------------------------------------------------------------------


# A one-pixel PNG; enough for the multipart shape to be valid before the
# permission check refuses it.
_PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000a49444154789c6360000002000154a24f5f0000000049454e44ae426082"
)


def test_soundboard_denies_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/soundboard",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), response.text


def test_soundboard_upload_denies_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    """A write is refused by the manage gate before any file is processed."""
    server, _channel = server_and_channel
    response = test_client.post(
        f"/api/v1/servers/{server.id}/soundboard",
        headers=_headers(second_test_user["token"]),
        data={"name": "boop"},
        files={"file": ("boop.wav", b"RIFF....WAVEfmt ", "audio/wav")},
    )
    assert _denied(response.status_code), response.text


def test_soundboard_allows_the_owner(
    test_client, server_and_channel, test_user_with_token
) -> None:
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/soundboard",
        headers=_headers(test_user_with_token["token"]),
    )
    assert response.status_code == 200, response.text


# ---------------------------------------------------------------------------
# stickers
# ---------------------------------------------------------------------------


def test_server_stickers_deny_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    """The manager refuses with PermissionDeniedError; the route must map it.

    Regression: this route funnelled every exception through a blanket 500, so
    a non-member's refusal was indistinguishable from an outage -- the same
    class as the automod denials in ANNEX Y, and contradicted by the route's
    own documented 403.
    """
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/stickers",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), response.text


def test_server_stickers_allow_the_owner(
    test_client, server_and_channel, test_user_with_token
) -> None:
    server, _channel = server_and_channel
    response = test_client.get(
        f"/api/v1/servers/{server.id}/stickers",
        headers=_headers(test_user_with_token["token"]),
    )
    assert response.status_code == 200, response.text


def test_create_sticker_denies_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    server, _channel = server_and_channel
    response = test_client.post(
        f"/api/v1/servers/{server.id}/stickers",
        headers=_headers(second_test_user["token"]),
        data={"name": "wave"},
        files={"image": ("wave.png", _PNG_BYTES, "image/png")},
    )
    assert _denied(response.status_code), response.text


# ---------------------------------------------------------------------------
# threads
# ---------------------------------------------------------------------------


def test_channel_threads_do_not_leak_to_a_non_member(
    test_client, server_and_channel, test_user_with_token, second_test_user
) -> None:
    """A real thread exists, and the non-member still cannot see it.

    The listing is deliberately silent for a caller without ``channels.view``
    (it returns an empty list rather than a 403), so the assertion is on the
    payload: the isolation is what matters, not the status code.
    """
    _server, channel = server_and_channel
    created = test_client.post(
        f"/api/v1/channels/{channel.id}/threads",
        headers=_headers(test_user_with_token["token"]),
        json={"name": "owner-only-thread"},
    )
    assert created.status_code == 200, created.text

    owner_view = test_client.get(
        f"/api/v1/channels/{channel.id}/threads",
        headers=_headers(test_user_with_token["token"]),
    )
    assert owner_view.status_code == 200, owner_view.text
    assert [t["name"] for t in owner_view.json()] == ["owner-only-thread"]

    outsider_view = test_client.get(
        f"/api/v1/channels/{channel.id}/threads",
        headers=_headers(second_test_user["token"]),
    )
    assert outsider_view.status_code == 200, outsider_view.text
    assert outsider_view.json() == []


def test_create_thread_denies_a_non_member(
    test_client, server_and_channel, second_test_user
) -> None:
    _server, channel = server_and_channel
    response = test_client.post(
        f"/api/v1/channels/{channel.id}/threads",
        headers=_headers(second_test_user["token"]),
        json={"name": "hijacked-thread"},
    )
    assert _denied(response.status_code), response.text


# ---------------------------------------------------------------------------
# polls
# ---------------------------------------------------------------------------


@pytest.fixture
def owner_poll(modules, server_and_channel, test_user_with_token):
    """A real poll on a real message, created by the channel-visible owner."""
    _server, channel = server_and_channel
    owner = test_user_with_token["user"]
    message = modules.messaging.send_message(
        owner.id, channel.conversation_id, "poll host message"
    )
    return modules.polls.create_poll(owner.id, message.id, "Lunch?", ["yes", "no"])


def test_poll_denies_a_non_member(test_client, owner_poll, second_test_user) -> None:
    """The poll exists; the non-member simply may not read its channel."""
    response = test_client.get(
        f"/api/v1/polls/{owner_poll.id}",
        headers=_headers(second_test_user["token"]),
    )
    assert _denied(response.status_code), response.text


def test_poll_allows_the_owner(test_client, owner_poll, test_user_with_token) -> None:
    response = test_client.get(
        f"/api/v1/polls/{owner_poll.id}",
        headers=_headers(test_user_with_token["token"]),
    )
    assert response.status_code == 200, response.text
    assert response.json()["question"] == "Lunch?"
