"""Fail-closed SDP answers: the manager must never invent ICE/DTLS credentials.

Before the fix, ``_build_sdp_from_transport`` filled a missing ICE ufrag/pwd
and DTLS fingerprint with fresh random values, and ``handle_sdp_offer_async``
silently fell back to a fully synthetic answer whenever the SFU failed to
negotiate. The client accepted an answer that could never connect, so a dead
call looked like a successful one.

These assert the manager now refuses, and that a transport carrying real
parameters still produces an answer built from those parameters.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.voice import signaling
from src.core.voice.signaling.exceptions import SDPError, SFUConnectionError
from src.core.voice.signaling.sfu.base import SFUTransport, TransportDirection

SAMPLE_SDP_OFFER = (
    "v=0\r\n"
    "o=- 1 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=ice-ufrag:clientufrag\r\n"
    "a=ice-pwd:clientpasswordclientpassword\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "a=mid:0\r\n"
    "a=rtcp-mux\r\n"
    "a=sendrecv\r\n"
    "a=rtpmap:111 opus/48000/2\r\n"
)

_SESSION_ID = "a" * 32

REAL_ICE = {"usernameFragment": "srvufrag", "password": "srv-password-0123456789"}
REAL_DTLS = {
    "role": "auto",
    "fingerprints": [
        {
            "algorithm": "sha-256",
            "value": (
                "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:"
                "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99"
            ),
        }
    ],
}


def _transport(ice=REAL_ICE, dtls=REAL_DTLS) -> SFUTransport:
    return SFUTransport(
        id="transport-1",
        direction=TransportDirection.SEND,
        ice_parameters=ice,
        ice_candidates=[
            {
                "foundation": "1",
                "component": 1,
                "protocol": "udp",
                "priority": 1,
                "ip": "10.0.0.1",
                "port": 5000,
                "type": "host",
            }
        ],
        dtls_parameters=dtls,
    )


class _StubSfu:
    """The ``_get_sfu_answer`` contract, with a controllable transport."""

    def __init__(
        self,
        transport: SFUTransport | None = None,
        apply_offer_result: dict | None = None,
        apply_offer_error: Exception | None = None,
    ) -> None:
        self._transport = transport
        self._apply_offer_result = apply_offer_result or {}
        self._apply_offer_error = apply_offer_error
        self.created = 0

    async def join_room(self, room_id: str, peer_id: str) -> dict:
        return {"routerRtpCapabilities": {}}

    async def complete_join(self, *args, **kwargs) -> bool:
        return True

    async def apply_remote_offer(
        self, room_id: str, peer_id: str, offer_sdp: str
    ) -> dict:
        if self._apply_offer_error is not None:
            raise self._apply_offer_error
        return self._apply_offer_result

    async def create_transport(self, room_id, peer_id, direction) -> SFUTransport:
        self.created += 1
        if self._transport is None:
            raise SFUConnectionError("no transport", backend="stub", url="stub://")
        return self._transport


@pytest.fixture
def wired(server_with_voice):
    """A real server + voice channel, with the signaling manager wired."""
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
    try:
        yield manager, owner, member1, voice_channel
    finally:
        try:
            asyncio.run(manager.close())
        except Exception:
            pass
        signaling._manager = None
        signaling._setup_complete = False


class TestBuildSdpRefusesToFabricate:
    def test_missing_ice_credentials_raises(self, wired):
        manager, *_ = wired
        with pytest.raises(SDPError, match="no ICE credentials"):
            manager._build_sdp_from_transport(
                SAMPLE_SDP_OFFER, _transport(ice={}), _SESSION_ID
            )

    def test_missing_dtls_fingerprint_raises(self, wired):
        manager, *_ = wired
        with pytest.raises(SDPError, match="no DTLS fingerprint"):
            manager._build_sdp_from_transport(
                SAMPLE_SDP_OFFER, _transport(dtls={}), _SESSION_ID
            )

    def test_answer_carries_the_transport_credentials_verbatim(self, wired):
        manager, *_ = wired
        answer = manager._build_sdp_from_transport(
            SAMPLE_SDP_OFFER, _transport(), _SESSION_ID
        )

        # A fabricated credential could not equal the transport's own value.
        assert "a=ice-ufrag:srvufrag" in answer
        assert "a=ice-pwd:srv-password-0123456789" in answer
        assert "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99" in answer
        assert "a=candidate:1 1 udp 1 10.0.0.1 5000 typ host" in answer


class TestOfferFailsClosed:
    def test_transport_without_credentials_fails_the_offer(self, wired):
        manager, _owner, member1, channel = wired
        manager._sfu = _StubSfu(transport=_transport(ice={}))

        with pytest.raises(SDPError):
            asyncio.run(
                manager.handle_sdp_offer_async(member1.id, channel.id, SAMPLE_SDP_OFFER)
            )

    def test_unnegotiable_backend_fails_the_offer(self, wired):
        # The Janus shape: apply_remote_offer returns nothing usable and
        # create_transport cannot produce a transport.
        manager, _owner, member1, channel = wired
        manager._sfu = _StubSfu(transport=None)

        with pytest.raises(SDPError):
            asyncio.run(
                manager.handle_sdp_offer_async(member1.id, channel.id, SAMPLE_SDP_OFFER)
            )

    def test_a_nonexistent_answer_is_never_synthesized(self, wired):
        """The dead offer must not leave a CONNECTED/CONNECTING connection."""
        manager, _owner, member1, channel = wired
        manager._sfu = _StubSfu(transport=None)

        with pytest.raises(SDPError):
            asyncio.run(
                manager.handle_sdp_offer_async(member1.id, channel.id, SAMPLE_SDP_OFFER)
            )
        connection = manager._connections.get(member1.id)
        assert connection is None or connection.local_sdp is None

    def test_real_transport_still_yields_an_answer(self, wired):
        manager, _owner, member1, channel = wired
        manager._sfu = _StubSfu(transport=_transport())

        answer = asyncio.run(
            manager.handle_sdp_offer_async(member1.id, channel.id, SAMPLE_SDP_OFFER)
        )

        assert answer.sdp_type.value == "answer"
        assert "a=ice-ufrag:srvufrag" in answer.sdp
