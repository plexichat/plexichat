"""Voice-message responses must carry snowflake ids as decimal strings.

``POST /features/voice-messages`` (and the multipart ``/upload`` variant) served
the voice message's ``id``, ``user_id``, ``conversation_id`` and
``metadata.voice_id`` as raw integers. A JS ``Number`` cannot hold a 64-bit
snowflake exactly, so a numeric id would be rounded by the client.
"""

import uuid
from unittest.mock import patch

import pytest

from src.utils import encryption

VOICE_URL = "/api/v1/features/voice-messages"


def _register_and_login(auth_manager, label="voice_user"):
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


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def pair(auth_manager):
    alice, alice_token = _register_and_login(auth_manager, "alice")
    bob, _ = _register_and_login(auth_manager, "bob")
    return {"alice": (alice, alice_token), "bob": bob}


def _valid_voice_payload(conversation_id):
    return {
        "conversation_id": conversation_id,
        "duration_ms": 5000,
        "filename": "voice.webm",
        "content_type": "audio/webm",
        "size": 1024,
        "url": "/api/v1/media/attachments/voice_1.webm",
    }


def test_voice_message_serves_string_snowflake_ids(
    test_client, messaging_manager, pair
):
    alice, alice_token = pair["alice"]
    bob = pair["bob"]
    dm = messaging_manager.create_dm(alice.id, bob.id)

    resp = test_client.post(
        VOICE_URL,
        json=_valid_voice_payload(str(dm.id)),
        headers=_headers(alice_token),
    )
    assert resp.status_code == 200, resp.text
    voice = resp.json()["voice_message"]

    assert isinstance(voice["id"], str)
    assert isinstance(voice["user_id"], str)
    assert voice["user_id"] == str(alice.id)
    assert isinstance(voice["conversation_id"], str)
    assert voice["conversation_id"] == str(dm.id)
    assert voice["message_id"] is None or isinstance(voice["message_id"], str)
    assert isinstance(voice["metadata"]["voice_id"], str)
    # Non-id fields keep their native shapes.
    assert voice["duration_ms"] == 5000
