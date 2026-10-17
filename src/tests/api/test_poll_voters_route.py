"""HTTP-level checks for GET /polls/{poll_id}/voters.

``PollManager.get_voters`` previously had no HTTP route and no module-level
proxy, so the creator roster it computes was unreachable from a client.
This pins the route's contract: creator-only access, visibility-aware
suppression, decimal-string ids on the wire (a JS ``Number`` cannot hold a
64-bit snowflake exactly), and the standard 400/403/404 mappings used by
the other poll routes.

A caller with no channel access gets 404 (existence is concealed from
non-members, matching the other poll routes); a channel-visible member
who is not the creator gets 403.
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture
def owned_server_channel(modules, test_user_with_token):
    """A real server (owned by the token user) with one text channel."""
    owner = test_user_with_token["user"]
    server = modules.servers.create_server(
        owner.id, f"Voters E2E {uuid.uuid4().hex[:6]}"
    )
    channel = modules.servers.create_channel(
        owner.id, server.id, "general", channel_type=modules.servers.ChannelType.TEXT
    )
    return server, channel


@pytest.fixture
def owner_poll(modules, owned_server_channel, test_user_with_token):
    """A real public poll on a real channel message, created by the owner."""
    _server, channel = owned_server_channel
    owner = test_user_with_token["user"]
    message = modules.messaging.send_message(
        owner.id, channel.conversation_id, "poll host message"
    )
    return modules.polls.create_poll(owner.id, message.id, "Lunch?", ["yes", "no"])


class TestGetPollVotersRoute:
    def test_creator_sees_string_voter_ids(
        self, test_client, auth_headers, modules, owner_poll, test_user_with_token
    ):
        owner = test_user_with_token["user"]
        modules.polls.vote(owner.id, owner_poll.id, [owner_poll.options[0].id])
        r = test_client.get(
            f"/api/v1/polls/{owner_poll.id}/voters", headers=auth_headers
        )
        assert r.status_code == 200, r.text
        voters = r.json()
        assert voters == [str(owner.id)]
        for voter_id in voters:
            assert isinstance(voter_id, str)
            assert voter_id.isdigit()

    def test_member_non_creator_is_forbidden(
        self,
        test_client,
        modules,
        owned_server_channel,
        owner_poll,
        second_test_user,
        second_auth_headers,
    ):
        server, _channel = owned_server_channel
        modules.servers.add_member(server.id, second_test_user["user"].id)
        r = test_client.get(
            f"/api/v1/polls/{owner_poll.id}/voters", headers=second_auth_headers
        )
        assert r.status_code == 403, r.text

    def test_non_member_gets_not_found(
        self, test_client, owner_poll, second_auth_headers
    ):
        r = test_client.get(
            f"/api/v1/polls/{owner_poll.id}/voters", headers=second_auth_headers
        )
        assert r.status_code == 404, r.text

    def test_unknown_poll_is_not_found(self, test_client, auth_headers):
        r = test_client.get("/api/v1/polls/999999999/voters", headers=auth_headers)
        assert r.status_code == 404, r.text

    def test_malformed_poll_id_is_bad_request(self, test_client, auth_headers):
        r = test_client.get("/api/v1/polls/not-an-id/voters", headers=auth_headers)
        assert r.status_code == 400, r.text

    def test_after_vote_poll_hides_voters(
        self,
        test_client,
        auth_headers,
        modules,
        owned_server_channel,
        test_user_with_token,
    ):
        from src.core.polls import PollResultsVisibility

        _server, channel = owned_server_channel
        owner = test_user_with_token["user"]
        message = modules.messaging.send_message(
            owner.id, channel.conversation_id, "secret ballot message"
        )
        poll = modules.polls.create_poll(
            owner.id,
            message.id,
            "Secret?",
            ["A", "B"],
            results_visibility=PollResultsVisibility.AFTER_VOTE,
        )
        modules.polls.vote(owner.id, poll.id, [poll.options[0].id])
        r = test_client.get(f"/api/v1/polls/{poll.id}/voters", headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json() == []
