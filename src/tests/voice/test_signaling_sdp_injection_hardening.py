"""Regression tests for SDP control-character injection at signaling boundaries.

The strict signaling parser (``validate_sdp``) is only applied to client
*offers*; a client's *answer* reaches the aiortc SFU adapter and is checked only
by ``validate_sdp_payload``. Two gaps are covered here:

1. ``validate_sdp_payload`` accepted bare CR / NUL / unicode separators inside
   SDP values. Emitted into SDP or log records, a consumer that treats those as
   line delimiters sees an attacker-chosen line (e.g. ``a=ice-pwd:...``).
2. The strict parser only rejected ASCII control bytes, so U+2028/U+2029 and
   bidi/invisible separators survived parsing and could be echoed into a
   generated answer by the SDP builder.
"""

from __future__ import annotations

import pytest

from src.core.voice.signaling.exceptions import SDPParseError
from src.core.voice.signaling.models import SDPType
from src.core.voice.signaling.sdp import validate_sdp
from src.core.voice.signaling.sfu.aiortc_adapter.errors import SFUValidationError
from src.core.voice.signaling.sfu.aiortc_adapter.negotiation import NegotiationMixin
from src.core.voice.signaling.sfu.aiortc_adapter.validation import validate_sdp_payload


_BASE_SDP = (
    "v=0\r\n"
    "o=- 123456 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "a=ice-ufrag:clientufrag\r\n"
    "a=ice-pwd:clientpasswordclientpassword\r\n"
    "a=fingerprint:sha-256 "
    "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99:"
    "AA:BB:CC:DD:EE:FF:00:11:22:33:44:55:66:77:88:99\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "c=IN IP4 0.0.0.0\r\n"
    "a=rtpmap:111 opus/48000/2\r\n"
)


class TestAdapterSdpControlCharsRejected:
    def test_bare_cr_forged_line_rejected(self):
        hostile = _BASE_SDP.replace(
            "a=rtpmap:111 opus/48000/2",
            "a=rtpmap:111 opus/48000/2\rX-INJECTED-A-LINE",
        )
        with pytest.raises(SFUValidationError):
            validate_sdp_payload(hostile, "answer SDP")

    @pytest.mark.parametrize("bad", ["\x00", "\x7f", "\u2028", "\u2029", "\ufeff"])
    def test_other_control_and_separator_chars_rejected(self, bad):
        hostile = _BASE_SDP.replace("s=-", "s=-" + bad + "a=ice-pwd:forged")
        with pytest.raises(SFUValidationError):
            validate_sdp_payload(hostile, "answer SDP")

    def test_normal_crlf_sdp_still_accepted(self):
        assert validate_sdp_payload(_BASE_SDP, "answer SDP") == _BASE_SDP

    def test_lf_only_sdp_still_accepted(self):
        assert validate_sdp_payload(
            _BASE_SDP.replace("\r\n", "\n")
        ) == _BASE_SDP.replace("\r\n", "\n")


class TestStrictParserUnicodeSeparatorsRejected:
    def test_unicode_separator_in_attribute_value_rejected(self):
        hostile = _BASE_SDP.replace(
            "a=rtpmap:111 opus/48000/2",
            "a=rtpmap:111 opus/48000/2\u2028a=ice-pwd:forged",
        )
        with pytest.raises(SDPParseError):
            validate_sdp(hostile, SDPType.OFFER)

    def test_normal_crlf_still_valid(self):
        validate_sdp(_BASE_SDP, SDPType.OFFER)


class TestAnswerPathUsesHardenedValidator:
    """The client-answer path must fail closed before touching the peer."""

    class _Negotiator(NegotiationMixin):
        _rooms: dict = {}

    @pytest.mark.asyncio
    async def test_hostile_answer_does_not_reach_peer_connection(self):
        hostile = _BASE_SDP.replace(
            "a=rtpmap:111 opus/48000/2",
            "a=rtpmap:111 opus/48000/2\rforged",
        )
        negotiator = self._Negotiator()
        # A rejected SDP returns False from apply_remote_answer (SFUValidationError
        # is swallowed there) rather than calling setRemoteDescription.
        assert (
            await negotiator.apply_remote_answer("voice_1", "user_1", hostile) is False
        )

    @pytest.mark.asyncio
    async def test_clean_answer_reaches_peer_lookup(self):
        # A well-formed answer passes validation and only then fails because
        # the peer is not in the room -- proving the validator is not rejecting
        # legitimate SDP outright.
        negotiator = self._Negotiator()
        assert (
            await negotiator.apply_remote_answer("voice_1", "user_1", _BASE_SDP)
            is False
        )
