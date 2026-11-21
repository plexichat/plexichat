"""Regression: a signed application webhook must not be accepted twice."""

import hashlib
import hmac
import time
import uuid

import pytest

from src.core.applications.exceptions import WebhookSignatureError

pytestmark = pytest.mark.applications


def test_signed_webhook_cannot_be_replayed(app_manager, test_user):
    app_manager.create_application(owner_id=test_user.id, name="Replay Reg App")
    body = ('{"type": 1, "n": "' + uuid.uuid4().hex + '"}').encode()
    timestamp = str(int(time.time()))
    secret = app_manager._config.get("webhook_signature_secret", "")
    signature = hmac.new(
        secret.encode(), timestamp.encode() + body, hashlib.sha256
    ).hexdigest()
    assert app_manager.verify_webhook_signature(body, signature, timestamp) is True
    with pytest.raises(WebhookSignatureError, match="replay"):
        app_manager.verify_webhook_signature(body, signature, timestamp)


def test_replay_cache_failure_fails_closed(app_manager, test_user, monkeypatch):
    app_manager.create_application(owner_id=test_user.id, name="Replay Fail App")
    body = ('{"type": 1, "n": "' + uuid.uuid4().hex + '"}').encode()
    timestamp = str(int(time.time()))
    secret = app_manager._config.get("webhook_signature_secret", "")
    signature = hmac.new(
        secret.encode(), timestamp.encode() + body, hashlib.sha256
    ).hexdigest()
    import src.core.applications.manager.webhook as webhook_mod

    def _boom(*args, **kwargs):
        raise RuntimeError("cache down")

    monkeypatch.setattr(webhook_mod, "cache_get", _boom, raising=False)
    monkeypatch.setattr(webhook_mod, "cache_set", _boom, raising=False)
    with pytest.raises(WebhookSignatureError):
        app_manager.verify_webhook_signature(body, signature, timestamp)
