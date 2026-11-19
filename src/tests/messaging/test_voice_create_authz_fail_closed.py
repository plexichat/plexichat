"""Fail-closed authorization for voice-message creation.

Regression: ``VoiceMessageService.create_voice_message`` previously guarded its
participation check with ``if participant_svc and ...``. When no checker was
supplied (the messaging module was unavailable, or a manager implementation
lacked ``_participant_svc``), the check was silently skipped: any authenticated
user could publish a voice message into a conversation they do not belong to.
Every other messaging write path (send, pin, forwarding, scheduled messages,
...) denies when the participation checker is missing; this pins the same
policy for voice messages.

Run with: pytest src/tests/messaging/test_voice_create_authz_fail_closed.py -q
"""

import pytest

from src.core.messaging.services.voice import VoiceMessageService

_VALID = dict(
    user_id=5,
    conversation_id=2,
    duration_ms=5000,
    filename="note.ogg",
    content_type="audio/ogg",
    size=4096,
    url="/api/v1/media/voice/note.ogg",
)


class _Participants:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    def is_participant(self, conversation_id, user_id):
        self.calls.append((conversation_id, user_id))
        return self.allowed


@pytest.mark.messaging
class TestVoiceCreateAuthzFailClosed:
    def test_missing_participant_service_denies(self):
        """No checker => deny before validating or building any record."""
        service = VoiceMessageService(None)

        with pytest.raises(PermissionError, match="Cannot verify"):
            service.create_voice_message(**_VALID)

    def test_non_participant_is_denied(self):
        checker = _Participants(allowed=False)
        service = VoiceMessageService(None)

        with pytest.raises(PermissionError, match="Not a participant"):
            service.create_voice_message(participant_svc=checker, **_VALID)

        assert checker.calls == [(2, 5)]

    def test_participant_is_allowed_and_attachment_is_built(self):
        checker = _Participants(allowed=True)
        service = VoiceMessageService(None)

        result = service.create_voice_message(participant_svc=checker, **_VALID)

        assert checker.calls == [(2, 5)]
        assert result["user_id"] == 5
        assert result["conversation_id"] == 2
        assert result["attachment_data"]["filename"] == "note.ogg"
        assert result["metadata"]["format"] == "ogg"

    def test_participant_still_gets_payload_validation(self):
        """Fail-closed authz must not weaken the existing metadata checks."""
        checker = _Participants(allowed=True)
        service = VoiceMessageService(None)
        payload = dict(_VALID, content_type="text/html")

        with pytest.raises(ValueError, match="audio content type"):
            service.create_voice_message(participant_svc=checker, **payload)
