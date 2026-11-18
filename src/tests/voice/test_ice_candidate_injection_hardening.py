"""Regression tests for ICE-candidate control-char injection and size caps.

The candidate string is attacker-supplied trickle-ICE input that is stored on
the signaling connection and echoed to clients (``ICECandidate.to_dict``) and
into SDP/log records. It must be bound and fully grammar-matched, never treated
as a free-form string with a valid prefix.
"""

import pytest

from src.core.voice.signaling.ice import (
    ICECandidateManager,
    ICECandidateParser,
    MAX_CANDIDATES_PER_SESSION,
    MAX_ICE_CANDIDATE_LENGTH,
    parse_ice_candidate,
    validate_ice_candidate,
)
from src.core.voice.signaling.exceptions import ICECandidateError

BASE = "candidate:1 1 udp 2130706431 192.168.1.100 54321 typ host"


class TestCandidateControlCharInjection:
    """A valid prefix plus a forged suffix must not be accepted verbatim."""

    @pytest.mark.parametrize(
        "suffix",
        [
            "\r\na=ice-pwd:evil",  # bare/newline + forged SDP attribute
            "\rSet-Cookie: forged=1",  # bare CR
            "\x00a=fingerprint:deadbeef",  # NUL
            "\x7fa=ice-ufrag:evil",  # DEL
            "\u2028a=ice-pwd:evil",  # Unicode LINE SEPARATOR
            "\u2029a=ice-pwd:evil",  # Unicode PARAGRAPH SEPARATOR
            "\u0085a=ice-pwd:evil",  # NEXT LINE
            "\u202ea=ice-pwd:evil",  # bidi override
            "\ufeffa=ice-pwd:evil",  # BOM
        ],
    )
    def test_control_chars_rejected(self, suffix):
        poisoned = BASE + suffix
        with pytest.raises(ICECandidateError):
            ICECandidateParser().parse(poisoned)
        with pytest.raises(ICECandidateError):
            parse_ice_candidate(poisoned)
        assert validate_ice_candidate(poisoned) is False

    def test_trailing_garbage_rejected(self):
        # ``regex.match`` used to accept the prefix and silently drop the tail
        # while the raw (attacker-controlled) string was stored in full.
        poisoned = BASE + " X-UNKNOWN-EXTENSION evil"
        with pytest.raises(ICECandidateError):
            ICECandidateParser().parse(poisoned)
        assert validate_ice_candidate(poisoned) is False

    def test_to_candidate_never_stores_control_chars(self):
        # Even the high-level helper must not persist a poisoned value.
        with pytest.raises(ICECandidateError):
            ICECandidateParser().to_candidate(BASE + "\r\na=ice-pwd:evil")

    def test_valid_candidate_still_accepted(self):
        parsed = ICECandidateParser().parse(BASE)
        assert parsed["type"] == "host"
        assert parsed["address"] == "192.168.1.100"
        assert validate_ice_candidate(BASE) is True


class TestCandidateSizeBounds:
    """Oversized candidates and unbounded candidate counts are refused."""

    def test_oversized_candidate_rejected(self):
        huge = "candidate:" + "A" * (MAX_ICE_CANDIDATE_LENGTH + 1) + \
            " 1 udp 2130706431 192.168.1.100 54321 typ host"
        with pytest.raises(ICECandidateError):
            ICECandidateParser().parse(huge)
        assert validate_ice_candidate(huge) is False

    def test_non_string_candidate_rejected(self):
        with pytest.raises(ICECandidateError):
            ICECandidateParser().parse(None)

    def test_candidate_count_per_session_capped(self):
        manager = ICECandidateManager()
        for i in range(MAX_CANDIDATES_PER_SESSION):
            candidate = (
                f"candidate:1 1 udp 2130706431 192.168.1.100 {1024 + i} typ host"
            )
            manager.add_candidate("session", candidate)

        assert len(manager.get_candidates("session")) == MAX_CANDIDATES_PER_SESSION
        with pytest.raises(ICECandidateError):
            manager.add_candidate("session", BASE)

        # A different session keeps its own budget.
        manager.add_candidate("other", BASE)
        assert len(manager.get_candidates("other")) == 1

    def test_manager_rejects_poisoned_candidate(self):
        manager = ICECandidateManager()
        with pytest.raises(ICECandidateError):
            manager.add_candidate("session", BASE + "\r\na=ice-pwd:evil")
        assert manager.get_candidates("session") == []
