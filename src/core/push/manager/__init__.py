"""Push notification manager package.

This package replaces the former single-file ``manager.py``. The public
surface is intentionally unchanged: importing :class:`PushManager` from
``src.core.push.manager`` keeps working for every existing caller, while
the internals are split into one mixin per responsibility.

``PUSH_TOKEN_BLIND_INDEX_SCOPE`` is re-exported for callers that imported
the blind-index scope from this module before the split.
"""

from .apns import ApnsMixin
from .base import PUSH_TOKEN_BLIND_INDEX_SCOPE as PUSH_TOKEN_BLIND_INDEX_SCOPE
from .delivery import DeliveryMixin
from .fcm import FcmMixin
from .tokens import TokenMixin
from .web_push import WebPushMixin


class PushManager(
    TokenMixin,
    DeliveryMixin,
    WebPushMixin,
    FcmMixin,
    ApnsMixin,
):
    """Core push notification manager."""


__all__ = ["PUSH_TOKEN_BLIND_INDEX_SCOPE", "PushManager"]
