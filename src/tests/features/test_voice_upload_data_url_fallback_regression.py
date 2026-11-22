"""The multipart voice upload must not embed audio in a ``data:`` URL.

The JSON endpoint rejects ``data:`` URLs outright (unbounded length and an
active-content sink). The multipart endpoint used to fall back to
``data:<content_type>;base64,...`` whenever the media module was unavailable,
storing a multi-megabyte string in the message's ``url`` column and echoing it
on every read. Storage now fails closed instead.
"""

import uuid
from unittest.mock import patch

from src.utils import encryption

UPLOAD_URL = "/api/v1/features/voice-messages/upload"


def _register_and_login(auth_manager, label="voice_upload"):
    unique = uuid.uuid4().hex[:8]
    username = f"{label}_{unique}"
    email = f"{label}_{unique}@test.local"
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username, email=email, password="TestPass123!"
        )
    with patch.object(encryption, "verify_password", return_value=True):
        result = auth_manager.login(username, "TestPass123!")
    return user, result.token


def test_voice_upload_fails_closed_when_media_unavailable(test_client, auth_manager):
    _, token = _register_and_login(auth_manager)

    with (
        patch("src.api.get_media", return_value=None),
        patch(
            "src.core.messaging.services.voice.VoiceMessageService.create_voice_message"
        ) as create_voice,
    ):
        resp = test_client.post(
            UPLOAD_URL,
            data={"conversation_id": "123456789", "duration_ms": "5000"},
            files={
                "audio": (
                    "voice.webm",
                    b"\x1a\x45\xdf\xa3test-audio-bytes",
                    "audio/webm",
                )
            },
            headers={"Authorization": f"Bearer {token}"},
        )

    assert resp.status_code == 500, resp.text
    # No message (and therefore no data: URL) may be persisted on this path.
    create_voice.assert_not_called()
