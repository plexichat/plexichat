"""
Regression tests for Agent 34 Wave-5 fixes (W5-A) in src/core/voice/.

Covers:
- C3: voice.connect enforcement on SDP-offer auto-connect
- C4: media token persistence + validation + revocation
- H1: join/leave race guarded by session (rejoin survives in-flight leave)
- H4: channel-id validated against bound connection on ICE/SDP ops
- M3: move_member destination checks (voice.connect + user_limit)
- M6: get_voice_server_info requires voice.connect
- M7: screen share permission + stop channel match
"""

import asyncio

import pytest

from src.core.voice import signaling
from src.core.voice.exceptions import ChannelFullError, PermissionDeniedError
from src.core.voice.signaling.exceptions import NotConnectedError

MINIMAL_SDP = (
    "v=0\r\n"
    "o=- 123 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
)


@pytest.fixture
def w5a(server_with_voice):
    """Set up the signaling module against a fresh server-with-voice."""
    (
        owner,
        member1,
        member2,
        server,
        voice_channel,
        stage_channel,
        servers,
        voice,
    ) = server_with_voice

    signaling.setup(voice_module=voice, events_module=None, sfu_backend="mediasoup")
    manager = signaling._get_manager()

    return {
        "owner": owner,
        "member1": member1,
        "member2": member2,
        "server": server,
        "voice_channel": voice_channel,
        "stage_channel": stage_channel,
        "servers": servers,
        "voice": voice,
        "manager": manager,
    }


def _deny_connect(ctx, channel):
    everyone_role = next(
        r
        for r in ctx["servers"].get_roles(ctx["owner"].id, ctx["server"].id)
        if r.is_default
    )
    ctx["servers"].set_channel_override(
        ctx["owner"].id,
        channel.id,
        "role",
        everyone_role.id,
        deny={"voice.connect": True},
    )


class TestC3SdpAutoConnectPermission:
    def test_sdp_auto_connect_denied_without_connect(self, w5a):
        """C3: SDP offer must not auto-connect a user lacking voice.connect."""
        restricted = w5a["servers"].create_channel(
            w5a["owner"].id,
            w5a["server"].id,
            "restricted-w5a-c3",
            channel_type=w5a["servers"].ChannelType.VOICE,
        )
        _deny_connect(w5a, restricted)

        with pytest.raises(PermissionDeniedError):
            asyncio.run(
                w5a["manager"].handle_sdp_offer_async(
                    w5a["member1"].id, restricted.id, MINIMAL_SDP
                )
            )
        assert w5a["member1"].id not in w5a["manager"]._connections


class TestC4MediaToken:
    def test_token_persisted_and_validated(self, w5a):
        info = signaling.get_voice_server_info(
            w5a["member1"].id, w5a["voice_channel"].id
        )

        assert info.token
        # Token is embedded in the media-plane endpoint (mediasoup_ws URL).
        assert f"token={info.token}" in info.endpoint
        assert signaling.validate_media_token(info.token, w5a["member1"].id)
        # Bound to the issued channel.
        assert signaling.validate_media_token(
            info.token, w5a["member1"].id, w5a["voice_channel"].id
        )
        # Another user's id must not validate.
        assert not signaling.validate_media_token(info.token, w5a["member2"].id)
        assert not signaling.validate_media_token("not-a-real-token")

    def test_connection_carries_token_and_revoked_on_disconnect(self, w5a):
        signaling.create_voice_connection(w5a["member1"].id, w5a["voice_channel"].id)
        conn = w5a["manager"]._connections[w5a["member1"].id]
        token = conn.media_token
        assert token

        asyncio.run(signaling.disconnect_voice_async(w5a["member1"].id))

        assert not signaling.validate_media_token(token)

    def test_validate_requires_token(self, w5a):
        assert not signaling.validate_media_token(None)
        assert not signaling.validate_media_token("")


class TestH1JoinLeaveRace:
    def test_rejoin_survives_inflight_disconnect(self, w5a):
        manager = w5a["manager"]
        user_id = w5a["member1"].id
        channel_id = w5a["voice_channel"].id

        signaling.create_voice_connection(user_id, channel_id)
        conn = manager._connections[user_id]
        conn.sfu_room_id = "room_w5a_race"
        conn.sfu_peer_id = f"user_{user_id}"

        release = asyncio.Event()

        class StubSFU:
            async def leave_room(self, room_id, peer_id):
                await release.wait()

        manager._sfu = StubSFU()

        async def scenario():
            task = asyncio.create_task(manager.disconnect_voice_async(user_id))
            await asyncio.sleep(0)

            # User rejoins while the old session's SFU leave is in flight.
            signaling.create_voice_connection(user_id, channel_id)
            new_conn = manager._connections[user_id]

            release.set()
            await task

            # The stale disconnect must NOT have deleted the newer connection.
            assert manager._connections.get(user_id) is new_conn
            assert new_conn.session_id != conn.session_id

        try:
            asyncio.run(scenario())
        finally:
            manager._sfu = None

    def test_stale_scheduled_disconnect_spares_rejoined_session(self, w5a):
        """H1 follow-up: a disconnect scheduled pre-rejoin (expected_session_id)
        must bail out instead of cleaning up / revoking the newer session."""
        manager = w5a["manager"]
        user_id = w5a["member1"].id
        channel_id = w5a["voice_channel"].id

        first = signaling.create_voice_connection(user_id, channel_id)
        stale_session = manager._connections[user_id].session_id

        # Rejoin replaces the connection with a NEW session before the
        # scheduled teardown runs (what create_task deferral allows).
        del manager._connections[user_id]
        second = signaling.create_voice_connection(user_id, channel_id)

        assert (
            asyncio.run(
                manager.disconnect_voice_async(
                    user_id, channel_id, expected_session_id=stale_session
                )
            )
            is False
        )

        # The rejoined session survived with its media token intact.
        conn = manager._connections.get(user_id)
        assert conn is not None
        assert conn.session_id != stale_session
        assert signaling.validate_media_token(second.token, user_id)
        # The OLD session's token was correctly left alone too (it belongs
        # to a different session than the live one).
        assert signaling.validate_media_token(first.token, user_id) is True

        # A matching expected_session_id still performs the full teardown.
        assert (
            asyncio.run(
                manager.disconnect_voice_async(
                    user_id,
                    channel_id,
                    expected_session_id=conn.session_id,
                )
            )
            is True
        )
        assert user_id not in manager._connections
        assert signaling.validate_media_token(second.token, user_id) is False


class TestH4ChannelBinding:
    def test_ice_candidate_channel_mismatch_rejected(self, w5a):
        other_channel = w5a["servers"].create_channel(
            w5a["owner"].id,
            w5a["server"].id,
            "other-w5a",
            channel_type=w5a["servers"].ChannelType.VOICE,
        )
        signaling.create_voice_connection(w5a["member1"].id, w5a["voice_channel"].id)

        assert (
            signaling.handle_ice_candidate(
                w5a["member1"].id,
                other_channel.id,
                "candidate:1 1 udp 1 1.1.1.1 5000 typ host",
            )
            is False
        )

    def test_sdp_offer_channel_mismatch_rejected(self, w5a):
        other_channel = w5a["servers"].create_channel(
            w5a["owner"].id,
            w5a["server"].id,
            "other-w5a-sdp",
            channel_type=w5a["servers"].ChannelType.VOICE,
        )
        signaling.create_voice_connection(w5a["member1"].id, w5a["voice_channel"].id)

        with pytest.raises(NotConnectedError):
            asyncio.run(
                w5a["manager"].handle_sdp_offer_async(
                    w5a["member1"].id, other_channel.id, MINIMAL_SDP
                )
            )


class TestM6CredentialIssuance:
    def test_view_only_member_denied_credentials(self, w5a):
        restricted = w5a["servers"].create_channel(
            w5a["owner"].id,
            w5a["server"].id,
            "restricted-w5a-m6",
            channel_type=w5a["servers"].ChannelType.VOICE,
        )
        _deny_connect(w5a, restricted)

        with pytest.raises(PermissionDeniedError):
            signaling.get_voice_server_info(w5a["member1"].id, restricted.id)


class TestM7ScreenShare:
    def test_sdp_video_track_requires_speak_permission(self, w5a):
        """M7 media plane: an SDP offer publishing a video/screen track must
        be rejected for users without voice.speak, even though they never
        call start_screen_share."""
        everyone_role = next(
            r
            for r in w5a["servers"].get_roles(w5a["owner"].id, w5a["server"].id)
            if r.is_default
        )
        w5a["servers"].set_channel_override(
            w5a["owner"].id,
            w5a["voice_channel"].id,
            "role",
            everyone_role.id,
            deny={"voice.speak": True},
        )

        signaling.create_voice_connection(w5a["member1"].id, w5a["voice_channel"].id)

        video_sdp = MINIMAL_SDP + (
            "m=video 9 UDP/TLS/RTP/SAVPF 96\r\na=rtpmap:96 VP8/90000\r\n"
        )
        with pytest.raises(PermissionDeniedError):
            asyncio.run(
                w5a["manager"].handle_sdp_offer_async(
                    w5a["member1"].id, w5a["voice_channel"].id, video_sdp
                )
            )

    def test_start_requires_speak_permission(self, w5a):
        everyone_role = next(
            r
            for r in w5a["servers"].get_roles(w5a["owner"].id, w5a["server"].id)
            if r.is_default
        )
        w5a["servers"].set_channel_override(
            w5a["owner"].id,
            w5a["voice_channel"].id,
            "role",
            everyone_role.id,
            deny={"voice.speak": True},
        )

        signaling.create_voice_connection(w5a["member1"].id, w5a["voice_channel"].id)

        with pytest.raises(PermissionDeniedError):
            signaling.start_screen_share(w5a["member1"].id, w5a["voice_channel"].id)

    def test_stop_verifies_channel_match(self, w5a):
        other_channel = w5a["servers"].create_channel(
            w5a["owner"].id,
            w5a["server"].id,
            "other-w5a-ss",
            channel_type=w5a["servers"].ChannelType.VOICE,
        )
        signaling.create_voice_connection(w5a["member1"].id, w5a["voice_channel"].id)
        signaling.start_screen_share(w5a["member1"].id, w5a["voice_channel"].id)

        assert signaling.stop_screen_share(w5a["member1"].id, other_channel.id) is False
        # The share must still be active after the mismatched stop attempt.
        conn = w5a["manager"]._connections[w5a["member1"].id]
        assert conn.screen_share.active is True

        assert (
            signaling.stop_screen_share(w5a["member1"].id, w5a["voice_channel"].id)
            is True
        )


class TestM3MoveMemberDestination:
    def test_move_into_full_channel_rejected(self, w5a):
        dest = w5a["servers"].create_channel(
            w5a["owner"].id,
            w5a["server"].id,
            "dest-full-w5a",
            channel_type=w5a["servers"].ChannelType.VOICE,
        )
        w5a["voice"].set_user_limit(w5a["owner"].id, dest.id, 1)

        w5a["voice"].join_channel(w5a["member1"].id, w5a["voice_channel"].id)
        w5a["voice"].join_channel(w5a["member2"].id, dest.id)

        with pytest.raises(ChannelFullError):
            w5a["voice"].move_member(w5a["owner"].id, w5a["member1"].id, dest.id)

    def test_move_into_no_connect_channel_rejected(self, w5a):
        dest = w5a["servers"].create_channel(
            w5a["owner"].id,
            w5a["server"].id,
            "dest-noperm-w5a",
            channel_type=w5a["servers"].ChannelType.VOICE,
        )
        _deny_connect(w5a, dest)

        w5a["voice"].join_channel(w5a["member1"].id, w5a["voice_channel"].id)

        with pytest.raises(PermissionDeniedError) as exc_info:
            w5a["voice"].move_member(w5a["owner"].id, w5a["member1"].id, dest.id)

        assert exc_info.value.permission == "voice.connect"


class TestH2StaleConnectionSweeper:
    def test_sweeper_disconnects_idle_connections(self, w5a):
        manager = w5a["manager"]
        user_id = w5a["member1"].id

        info = signaling.create_voice_connection(user_id, w5a["voice_channel"].id)
        conn = manager._connections[user_id]
        conn.last_activity = conn.last_activity - 10 * 60 * 60 * 1000

        swept = asyncio.run(manager.sweep_stale_connections(max_idle_ms=1000))

        assert user_id in swept
        assert user_id not in manager._connections
        assert not signaling.validate_media_token(info.token)
