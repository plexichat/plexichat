"""Regression tests for voice signaling response-boundary hardening.

Three real bypasses are covered:

1. TURN URLs rejected by ``validate_ice_url`` were still delivered to clients
   because ``ICEServerBuilder`` forwarded the raw config list to the credential
   generator (and the generator never validated its own URLs).
2. A bare CR / control byte embedded in an SDP line survived parsing and was
   echoed into the generated answer, letting a caller inject SDP lines.
3. Repeating a critical SDP attribute (ice-ufrag/ice-pwd/fingerprint) made the
   parser store a list, which the validator skipped entirely; media-level
   attributes were not validated at all.
"""

from __future__ import annotations

import pytest

from src.core.voice.signaling.exceptions import (
    SDPParseError,
    SDPValidationError,
)
from src.core.voice.signaling.models import SDPType
from src.core.voice.signaling.sdp import parse_sdp, validate_sdp
from src.core.voice.signaling.turn import (
    ICEServerBuilder,
    TURNCredentialGenerator,
    generate_turn_credentials,
)


_GOOD_TURN = "turn:turn.example.com:3478"
_BAD_URLS = [
    "javascript:alert(1)",
    "https://attacker.example.com/collect",
    "stun:host<script>",
]


class TestTurnUrlsAreValidatedAtEveryExit:
    def test_iceserver_builder_filters_credential_urls(self):
        builder = ICEServerBuilder(
            turn_urls=[_GOOD_TURN, *_BAD_URLS],
            turn_secret="secret",
            turn_ttl=3600,
        )
        creds = builder.get_turn_credentials(123)
        assert creds is not None
        assert creds.urls == [_GOOD_TURN]

        turn_servers = [
            s for s in builder.build(123) if any(u.startswith("turn") for u in s.urls)
        ]
        assert len(turn_servers) == 1
        assert turn_servers[0].urls == [_GOOD_TURN]

    def test_all_invalid_turn_urls_yield_no_credentials(self):
        builder = ICEServerBuilder(turn_urls=list(_BAD_URLS), turn_secret="secret")
        assert builder.get_turn_credentials(123) is None
        assert not any(
            any(u.startswith("turn") for u in s.urls) for s in builder.build(123)
        )

    def test_generator_and_helper_validate_their_own_urls(self):
        generator = TURNCredentialGenerator("secret", [_GOOD_TURN, *_BAD_URLS])
        assert generator.generate(7).urls == [_GOOD_TURN]
        assert generate_turn_credentials(
            7, "secret", [_GOOD_TURN, *_BAD_URLS]
        ).urls == [_GOOD_TURN]


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


class TestSdpControlCharactersRejected:
    def test_bare_cr_in_line_value_rejected(self):
        hostile = _BASE_SDP.replace(
            "a=rtpmap:111 opus/48000/2",
            "a=rtpmap:111 opus/48000/2\rX-INJECTED-A-LINE",
        )
        # The parser used to keep the bare CR in the attribute value, which the
        # answer builder then echoed verbatim. It must now be rejected at parse
        # time so no downstream consumer can see the injected line.
        with pytest.raises(SDPParseError):
            parse_sdp(hostile)
        with pytest.raises(SDPParseError):
            validate_sdp(hostile, SDPType.OFFER)

    def test_nul_byte_in_line_value_rejected(self):
        hostile = _BASE_SDP.replace("s=-\r\n", "s=-\x00injected\r\n")
        with pytest.raises(SDPParseError):
            validate_sdp(hostile, SDPType.OFFER)

    def test_normal_crlf_sdp_still_valid(self):
        validate_sdp(_BASE_SDP, SDPType.OFFER)


class TestSdpDuplicateAttributesValidated:
    @pytest.mark.parametrize(
        "line",
        [
            "a=ice-ufrag:../../evil\r\n",
            "a=ice-pwd:short\r\n",
            "a=fingerprint:md5 deadbeef\r\n",
        ],
    )
    def test_duplicate_bad_session_attribute_rejected(self, line):
        hostile = _BASE_SDP.replace("t=0 0\r\n", "t=0 0\r\n" + line)
        # A repeated attribute is stored as a list; the old validator skipped
        # list values completely.
        name = line.split(":")[0][2:]
        assert isinstance(parse_sdp(hostile)["attributes"][name], list)
        with pytest.raises(SDPValidationError):
            validate_sdp(hostile, SDPType.OFFER)

    def test_bad_media_level_fingerprint_rejected(self):
        hostile = _BASE_SDP.replace(
            "a=rtpmap:111 opus/48000/2\r\n",
            "a=fingerprint:sha-256 ZZ:NOT:HEX\r\na=rtpmap:111 opus/48000/2\r\n",
        )
        with pytest.raises(SDPValidationError):
            validate_sdp(hostile, SDPType.OFFER)


class TestRealClientCredentialsStillAccepted:
    """Stricter validation must not reject legitimate WebRTC credentials.

    aiortc emits sha-384/sha-512 fingerprints at media level and base64url
    (``-``/``_``) ICE tokens; these must keep validating after hardening.
    """

    def test_media_level_sha384_sha512_fingerprints_accepted(self):
        sdp = _BASE_SDP.replace(
            "a=rtpmap:111 opus/48000/2\r\n",
            "a=fingerprint:sha-384 " + "AA:" * 47 + "AA\r\n"
            "a=fingerprint:sha-512 " + "BB:" * 63 + "BB\r\n"
            "a=rtpmap:111 opus/48000/2\r\n",
        )
        validate_sdp(sdp, SDPType.OFFER)

    def test_base64url_ice_credentials_accepted(self):
        sdp = _BASE_SDP.replace("clientufrag", "u-frag_9A")
        sdp = sdp.replace("clientpasswordclientpassword", "a-b_C" * 5)
        validate_sdp(sdp, SDPType.OFFER)
