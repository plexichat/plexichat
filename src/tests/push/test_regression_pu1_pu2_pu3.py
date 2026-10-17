"""Regression tests for push bug fixes (PU1, PU2, PU3).

PU1: APNs transient errors (429/5xx) must NOT invalidate the token.
PU2: Web Push transient errors must NOT delete the subscription.
PU3: ``priority`` must be forwarded to FCM/APNs senders.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration


def _make_manager(db, auth_manager, pii_gen, username):
    from src.utils import encryption
    from src.core.push import setup
    from src.core.push.manager import PushManager

    setup(db, notifications_module=None)
    mgr = PushManager(db, notifications_module=None)
    with patch.object(encryption, "hash_password", return_value="fake_hash_$test"):
        user = auth_manager.register(
            username=username,
            email=pii_gen.email(),
            password="TestPass123!",
        )
    return mgr, user


class TestTransientKeepsTokenPU1PU2:
    def test_web_transient_keeps_subscription(self, db, auth_manager, pii_gen):
        mgr, user = _make_manager(db, auth_manager, pii_gen, "pu2web")
        mgr.register_token(user.id, "web-endpoint-keep", "web")

        # Simulate a transient Web Push failure (e.g. network blip / 503).
        mgr._send_web_push = lambda *a, **k: None

        sent = mgr.send_push(user.id, "Title", "Body")
        assert sent == 0

        tokens = mgr.get_user_tokens(user.id)
        assert any(t["token"] == "web-endpoint-keep" for t in tokens)

    def test_apns_transient_keeps_token(self, db, auth_manager, pii_gen):
        mgr, user = _make_manager(db, auth_manager, pii_gen, "pu1apns")
        mgr.register_token(user.id, "apns-token-keep", "ios")

        # Simulated transient APNs error (429 rate-limit / 500).
        mgr._send_apns = lambda *a, **k: None

        mgr.send_push(user.id, "Title", "Body")
        tokens = mgr.get_user_tokens(user.id)
        assert any(t["token"] == "apns-token-keep" for t in tokens)

    def test_permanent_failure_still_removes_token(self, db, auth_manager, pii_gen):
        mgr, user = _make_manager(db, auth_manager, pii_gen, "puperm")
        mgr.register_token(user.id, "apns-token-dead", "ios")

        # Genuinely invalid token (410 / BadDeviceToken).
        mgr._send_apns = lambda *a, **k: False

        mgr.send_push(user.id, "Title", "Body")
        tokens = mgr.get_user_tokens(user.id)
        assert not any(t["token"] == "apns-token-dead" for t in tokens)


class TestPriorityForwardingPU3:
    def test_priority_forwarded_to_fcm(self, db, auth_manager, pii_gen):
        mgr, user = _make_manager(db, auth_manager, pii_gen, "pu3fcm")
        mgr.register_token(user.id, "fcm-token-prio", "android")

        captured = {}

        def fake_fcm(token, title, body, data, config, priority="high"):
            captured["priority"] = priority
            return True

        mgr._send_fcm = fake_fcm
        mgr.send_push(user.id, "Title", "Body", priority="normal")
        assert captured["priority"] == "normal"

    def test_priority_forwarded_to_apns(self, db, auth_manager, pii_gen):
        mgr, user = _make_manager(db, auth_manager, pii_gen, "pu3apns")
        mgr.register_token(user.id, "apns-token-prio", "ios")

        captured = {}

        def fake_apns(token, title, body, data, badge, sound, config, priority="high"):
            captured["priority"] = priority
            return True

        mgr._send_apns = fake_apns
        mgr.send_push(user.id, "Title", "Body", priority="normal")
        assert captured["priority"] == "normal"
