"""Package-boundary tests for the push manager split."""

from __future__ import annotations

import threading

from src.core.push.manager import PUSH_TOKEN_BLIND_INDEX_SCOPE, PushManager
from src.core.push.manager.apns import ApnsMixin
from src.core.push.manager.base import PushManagerBase
from src.core.push.manager.delivery import DeliveryMixin
from src.core.push.manager.fcm import FcmMixin
from src.core.push.manager.tokens import TokenMixin
from src.core.push.manager.web_push import WebPushMixin

PUBLIC_METHODS = (
    "register_token",
    "unregister_token",
    "unregister_token_by_id",
    "get_user_tokens",
    "send_push",
    "send_bulk_push",
)


def test_public_surface_is_unchanged() -> None:
    assert issubclass(PushManager, PushManagerBase)
    for name in PUBLIC_METHODS:
        assert callable(getattr(PushManager, name)), name


def test_each_responsibility_is_its_own_mixin_module() -> None:
    for mixin in (TokenMixin, DeliveryMixin, WebPushMixin, FcmMixin, ApnsMixin):
        assert issubclass(PushManager, mixin)
    assert PushManager.register_token.__module__ == TokenMixin.__module__
    assert PushManager.send_push.__module__ == DeliveryMixin.__module__
    assert PushManager._send_web_push.__module__ == WebPushMixin.__module__
    assert PushManager._send_fcm.__module__ == FcmMixin.__module__
    assert PushManager._send_apns.__module__ == ApnsMixin.__module__


def test_class_level_helpers_stay_callable_unbound() -> None:
    # Routes call this as ``PushManager._sanitize_token_record(record)``.
    assert PushManager._sanitize_token_record({"token": "device-token-1234"}) == {
        "token_hint": "…1234"
    }
    assert PUSH_TOKEN_BLIND_INDEX_SCOPE == "push_token"
    # APNs JWT caching is manager state shared by every instance; the
    # existing APNs tests reach it through the class.
    assert isinstance(PushManager._apns_jwt_lock, type(threading.Lock()))
    assert isinstance(PushManager._apns_jwt_cache, dict)
