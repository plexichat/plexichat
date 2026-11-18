"""Regression tests for bounded media-token state in signaling.

``get_voice_server_info`` mints a bearer token on every call, including the
read-only ``GET /channels/{id}/info`` route. Those tokens used to accumulate
in ``SignalingManager._media_tokens`` without any cap, giving an authenticated
caller a memory-exhaustion vector via repeated info requests. These tests pin
the registry bound and the expiry reclamation.
"""

import pytest

from src.core.voice import signaling
from src.core.voice.signaling.manager import connection_mixin


@pytest.fixture
def ctx(server_with_voice):
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
    return {
        "manager": signaling._get_manager(),
        "user_id": member1.id,
        "channel_id": voice_channel.id,
    }


def test_media_token_registry_is_bounded(monkeypatch, ctx):
    monkeypatch.setattr(connection_mixin, "MAX_MEDIA_TOKENS", 4)
    manager = ctx["manager"]
    issued = []
    for _ in range(25):
        info = signaling.get_voice_server_info(ctx["user_id"], ctx["channel_id"])
        issued.append(info.token)

    assert len(manager._media_tokens) <= 4
    # The most recently minted token is still usable.
    assert signaling.validate_media_token(issued[-1], ctx["user_id"]) is True
    # The oldest tokens were evicted to make room.
    assert signaling.validate_media_token(issued[0], ctx["user_id"]) is False


def test_expired_tokens_reclaimed_on_mint(ctx):
    manager = ctx["manager"]
    now = manager._get_timestamp()
    stale_token = "expired-regression-token"
    manager._media_tokens[stale_token] = {
        "user_id": ctx["user_id"],
        "channel_id": ctx["channel_id"],
        "session_id": "expired-session",
        "issued_at": now - 100_000,
        "expires_at": now - 1,
    }

    info = signaling.get_voice_server_info(ctx["user_id"], ctx["channel_id"])

    assert stale_token not in manager._media_tokens
    assert signaling.validate_media_token(stale_token) is False
    assert signaling.validate_media_token(info.token, ctx["user_id"]) is True
