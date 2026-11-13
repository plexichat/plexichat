"""Firebase Cloud Messaging transport."""

import importlib
from typing import Any, Dict, Optional, cast

import utils.logger as logger

from .base import PushManagerBase


class FcmMixin(PushManagerBase):
    """Deliver to Android devices through FCM."""

    def _send_fcm(
        self,
        token: str,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        push_config: Dict[str, Any],
        priority: str = "high",
    ) -> Optional[bool]:
        """Send via Firebase Cloud Messaging.

        Returns ``True`` on success, ``False`` when the token is permanently
        invalid (caller drops it), and ``None`` when FCM is unconfigured or a
        transient error occurred (token left intact).
        """
        if not self._fcm_available:
            # Log that push would have been sent (for debugging)
            logger.debug(f"FCM not configured - would send push to {token[:16]}...")
            return None

        try:
            # Try to use firebase-admin SDK
            fcm_messaging = cast(
                Any, importlib.import_module("firebase_admin.messaging")
            )

            message = fcm_messaging.Message(
                token=token,
                notification=fcm_messaging.Notification(
                    title=title,
                    body=body,
                ),
                data={k: str(v) for k, v in (data or {}).items()},
                android=fcm_messaging.AndroidConfig(
                    priority=priority if priority in ("high", "normal") else "high",
                ),
            )

            response = fcm_messaging.send(message)
            logger.debug(f"FCM push sent: {response}")
            return True
        except ImportError:
            logger.warning("firebase-admin not installed - FCM push unavailable")
            return None
        except Exception as e:
            error_str = str(e).lower()
            if (
                "invalid" in error_str
                or "notregistered" in error_str
                or "unregistered" in error_str
            ):
                logger.warning(f"Invalid FCM token: {token[:16]}... - {e}")
                return False
            # Any other FCM error is transient/auth/config related and must not
            # invalidate an otherwise-valid token.
            logger.warning(f"FCM push skipped (transient) for {token[:16]}... - {e}")
            return None
