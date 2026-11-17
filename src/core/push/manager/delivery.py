"""Delivery fan-out across a user's registered devices."""

import asyncio
from typing import Any, Dict, List, Optional

import utils.logger as logger

from .base import PushManagerBase
from .async_fanout import (
    DEFAULT_CONCURRENCY,
    DEFAULT_ITEM_TIMEOUT_S,
    DEFAULT_QUEUE_SIZE,
    bounded_fanout,
)

# APNs caps an alert at ~4 KB and FCM/Web Push have similar practical limits.
# These strings are copied into every device's outbound payload, so an
# unbounded title/body from any caller would bloat every fan-out. Fail before
# the fan-out instead of serialising an arbitrarily large notification.
MAX_TITLE_LENGTH = 256
MAX_BODY_LENGTH = 4096

# Bounded async fan-out limits. The synchronous ``send_push``/``send_bulk_push``
# remain for blocking callers; async callers should use the ``*_async`` variants
# so a burst of notifications neither blocks the event loop nor spawns one task
# per device/user.
DEVICE_FANOUT_CONCURRENCY = DEFAULT_CONCURRENCY
DEVICE_FANOUT_QUEUE_SIZE = DEFAULT_QUEUE_SIZE
USER_FANOUT_CONCURRENCY = DEFAULT_CONCURRENCY
USER_FANOUT_QUEUE_SIZE = DEFAULT_QUEUE_SIZE
PUSH_SEND_TIMEOUT_S = DEFAULT_ITEM_TIMEOUT_S
#: Hard ceiling on users accepted by one bulk async fan-out (backpressure).
MAX_BULK_PUSH_USERS = 1000


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    text = str(value or "")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds maximum length of {maximum}")
    return text


class DeliveryMixin(PushManagerBase):
    """Send a notification to every valid device of a user."""

    def send_push(
        self,
        user_id: int,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        badge: Optional[int] = None,
        sound: str = "default",
        priority: str = "high",
    ) -> int:
        """
        Send a push notification to all of a user's devices.

        Args:
            user_id: ID of the target user
            title: Notification title
            body: Notification body text
            data: Optional data payload
            badge: Optional badge count
            sound: Sound name
            priority: Priority level

        Returns:
            Number of devices the notification was sent to
        """
        title = _bounded_text(title, "title", MAX_TITLE_LENGTH)
        body = _bounded_text(body, "body", MAX_BODY_LENGTH)

        tokens = self.get_user_tokens(user_id)
        if not tokens:
            return 0

        # Check user's notification settings
        if self._notifications:
            try:
                settings = self._notifications.get_notification_settings(user_id)
                if not settings.mobile_push:
                    return 0
            except Exception as e:
                logger.debug(f"Push settings lookup failed, delivering anyway: {e}")

        sent_count = 0
        invalid_tokens = []

        for token_record in tokens:
            token = token_record["token"]
            success = self._send_to_platform(
                token_record, title, body, data, badge, sound, priority
            )

            if success is True:
                sent_count += 1
                # Update last used
                now = self._get_timestamp()
                self._db.execute(
                    "UPDATE push_tokens SET last_used_at = ? WHERE token_index = ?",
                    (now, self._token_blind_index(token)),
                )
            elif success is False:
                # Token is genuinely invalid (e.g. subscription expired) -
                # mark for cleanup.
                invalid_tokens.append(token)
            # ``None`` means delivery was skipped because of missing
            # configuration (VAPID not set) or incomplete subscription data;
            # the token is neither counted nor invalidated.

        # Clean up invalid tokens
        for token in invalid_tokens:
            self._db.execute(
                "DELETE FROM push_tokens WHERE token_index = ?",
                (self._token_blind_index(token),),
            )
            logger.debug(f"Removed invalid push token for user {user_id}")

        return sent_count

    async def send_push_async(
        self,
        user_id: int,
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
        badge: Optional[int] = None,
        sound: str = "default",
        priority: str = "high",
    ) -> int:
        """Bounded async counterpart of :meth:`send_push`.

        Before: the synchronous path sent to each device in a blocking loop,
        so one slow FCM/APNs call stalled the caller (and, when called from an
        async route via a worker thread, consumed a thread per notification).

        After: token lookup/settings run off-loop, device sends run as a fixed
        pool of tasks fed by a bounded queue, and each send has a timeout. One
        unreachable device cannot block or abort delivery to the others.
        """
        title = _bounded_text(title, "title", MAX_TITLE_LENGTH)
        body = _bounded_text(body, "body", MAX_BODY_LENGTH)

        tokens = await asyncio.to_thread(self.get_user_tokens, user_id)
        if not tokens:
            return 0
        # One user cannot hold more than MAX_TOKENS_PER_USER tokens; bound here
        # too so a corrupt/lenient repository cannot blow up the fan-out.
        device_tokens = list(tokens)[: self.MAX_TOKENS_PER_USER]
        if not device_tokens:
            return 0

        if self._notifications:
            try:
                settings = await asyncio.to_thread(
                    self._notifications.get_notification_settings, user_id
                )
                if not settings.mobile_push:
                    return 0
            except Exception as e:
                logger.debug(f"Push settings lookup failed, delivering anyway: {e}")

        async def send_one(token_record: Dict[str, Any]) -> Optional[bool]:
            return await asyncio.to_thread(
                self._send_to_platform,
                token_record,
                title,
                body,
                data,
                badge,
                sound,
                priority,
            )

        outcomes = await bounded_fanout(
            device_tokens,
            send_one,
            concurrency=DEVICE_FANOUT_CONCURRENCY,
            max_queue=DEVICE_FANOUT_QUEUE_SIZE,
            timeout=PUSH_SEND_TIMEOUT_S,
        )

        sent_count = 0
        invalid_tokens: List[str] = []
        for token_record, outcome in zip(device_tokens, outcomes):
            if outcome is True:
                sent_count += 1
                now = self._get_timestamp()
                self._db.execute(
                    "UPDATE push_tokens SET last_used_at = ? WHERE token_index = ?",
                    (now, self._token_blind_index(token_record["token"])),
                )
            elif outcome is False:
                invalid_tokens.append(token_record["token"])

        for token in invalid_tokens:
            self._db.execute(
                "DELETE FROM push_tokens WHERE token_index = ?",
                (self._token_blind_index(token),),
            )
            logger.debug(f"Removed invalid push token for user {user_id}")

        return sent_count

    def _send_to_platform(
        self,
        token_record: Dict[str, Any],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]],
        badge: Optional[int],
        sound: str,
        priority: str,
    ) -> Optional[bool]:
        """Send push notification to a specific platform.

        Returns ``True`` on successful delivery, ``False`` when the token is
        invalid (caller should drop it), and ``None`` when delivery was
        skipped due to missing configuration/subscription data (the token is
        left intact).
        """
        try:
            import utils.config as config

            push_config = config.get("push_notifications", {})
        except Exception:
            push_config = {}

        token = token_record["token"]
        platform = token_record["platform"]
        if platform == "web":
            return self._send_web_push(
                token_record, title, body, data, push_config, priority
            )
        elif platform == "android":
            return self._send_fcm(token, title, body, data, push_config, priority)
        elif platform == "ios":
            return self._send_apns(
                token, title, body, data, badge, sound, push_config, priority
            )
        return False

    def send_bulk_push(
        self,
        user_ids: List[int],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Send push notification to multiple users."""
        total_sent = 0
        for user_id in user_ids:
            try:
                sent = self.send_push(user_id, title, body, data)
                total_sent += sent
            except Exception as e:
                logger.debug(f"Failed to send push to user {user_id}: {e}")
        return total_sent

    async def send_bulk_push_async(
        self,
        user_ids: List[int],
        title: str,
        body: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """Send push notifications to many users with bounded concurrency.

        Before: ``send_bulk_push`` looped synchronously over every user,
        blocking for the full duration of every device send. After: a fixed
        pool of user jobs is fed by a bounded queue (backpressure) and each
        user's device fan-out is itself bounded and async. The user list is
        capped so a single call cannot enqueue unbounded work.
        """
        title = _bounded_text(title, "title", MAX_TITLE_LENGTH)
        body = _bounded_text(body, "body", MAX_BODY_LENGTH)
        users = [int(uid) for uid in user_ids][:MAX_BULK_PUSH_USERS]
        if not users:
            return 0

        async def send_user(user_id: int) -> int:
            try:
                return await self.send_push_async(user_id, title, body, data)
            except Exception as e:
                logger.debug(f"Failed to send push to user {user_id}: {e}")
                return 0

        results = await bounded_fanout(
            users,
            send_user,
            concurrency=USER_FANOUT_CONCURRENCY,
            max_queue=USER_FANOUT_QUEUE_SIZE,
            timeout=PUSH_SEND_TIMEOUT_S * 2,
        )
        return sum(result or 0 for result in results)
