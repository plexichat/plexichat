"""Integration proof: DM spam detection actually fires in the live send path.

Regression coverage for the former blind spot where DMSpamDetector existed
but was never invoked by POST /channels/{id}/messages - meaning DMs had no
spam protection at all.
"""

from unittest.mock import patch

import pytest

from src.core.antispam.detector import DMSpamDetector
from src.core.antispam import reset_detector

pytestmark = pytest.mark.integration


def _register(auth_manager, name):
    from src.utils import encryption

    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        return auth_manager.register(
            username=name,
            email=f"{name}@example.com",
            password="TestPass123!",
        )


class TestDMSpamSendPath:
    def test_rate_limited_dm_rejected_by_send_route(
        self, test_client, auth_manager, messaging_manager, db
    ):
        reset_detector()

        suffix = id(object())
        sender = _register(auth_manager, f"dm_spam_sender_{suffix}")
        recipient = _register(auth_manager, f"dm_spam_target_{suffix}")

        from src.utils import encryption

        with patch.object(encryption, "verify_password", return_value=True):
            login = auth_manager.login(sender.username, "TestPass123!")
        headers = {"Authorization": f"Bearer {login.token}"}

        dm = messaging_manager.create_dm(sender.id, recipient.id)
        url = f"/api/v1/channels/{dm.id}/messages"

        # A benign message passes and is delivered.
        first = test_client.post(url, json={"content": "hello there"}, headers=headers)
        assert first.status_code == 200

        # Hammering past the per-conversation rate limit must be rejected.
        blocked_response = None
        for i in range(2, DMSpamDetector.DEFAULT_RATE_LIMIT + 3):
            response = test_client.post(
                url, json={"content": f"burst message {i}"}, headers=headers
            )
            if response.status_code != 200:
                blocked_response = response
                break

        assert blocked_response is not None, (
            "DM spam detector never fired in the send path"
        )
        assert blocked_response.status_code == 403
        payload = blocked_response.json()
        assert payload["error"]["message"] == "Message blocked by spam protection"

        # The detector persisted a spam event, proving it ran in-path.
        event = db.fetch_one(
            "SELECT COUNT(*) AS c FROM dm_spam_events "
            "WHERE sender_id = ? AND recipient_id = ? AND event_type = 'rate_limited'",
            (sender.id, recipient.id),
        )
        assert event["c"] >= 1
