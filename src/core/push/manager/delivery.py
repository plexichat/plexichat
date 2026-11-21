"""Delivery fan-out across a user's registered devices."""

import asyncio
import json
import threading
from collections import OrderedDict
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

# ``data`` and ``sound`` are copied into EVERY device's outbound payload just
# like title/body. title/body were bounded but these were not: one caller
# supplying a multi-megabyte ``data`` dict (or a giant ``sound`` string) would
# make the fan-out serialise that blob once per device/user. Bound them at the
# same boundary -- before any fan-out work is created. APNs/ FCM / Web Push have
# their own per-transport limits too, but the gate here is the one that applies
# uniformly to all three and keeps the queue/task pump bounded.
MAX_SOUND_LENGTH = 256
MAX_PUSH_DATA_KEYS = 64
MAX_PUSH_DATA_KEY_LENGTH = 256
MAX_PUSH_DATA_VALUE_BYTES = 4096
MAX_PUSH_DATA_TOTAL_BYTES = 64 * 1024

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

# A single announcement fan-out validates the SAME ``data`` mapping once per
# recipient (``send_push`` -> ``_bounded_data``). Every value is re-encoded with
# ``json.dumps`` just to measure its byte size, even though the six id fields
# and the mention type are identical for all recipients of one message. Memoize
# the derived size by the full canonical input (type + value): a changed value
# produces a different key and can never observe a stale size. The cache is a
# bounded LRU so a flood of distinct payloads cannot retain unbounded values,
# and values already past the byte cap never become cache keys.
_MAX_DATA_VALUE_SIZE_CACHE_ENTRIES = 256
_MAX_DATA_VALUE_CACHE_KEY_CHARS = MAX_PUSH_DATA_VALUE_BYTES
_DATA_VALUE_SIZE_CACHE: "OrderedDict[tuple, int]" = OrderedDict()
_DATA_VALUE_SIZE_CACHE_LOCK = threading.Lock()
_DATA_VALUE_SIZE_STATS = {"hits": 0, "misses": 0}


def get_push_data_serialization_stats() -> Dict[str, int]:
    """Hit/miss counters and size of the shared push-data size cache."""
    with _DATA_VALUE_SIZE_CACHE_LOCK:
        return {
            "size": len(_DATA_VALUE_SIZE_CACHE),
            "max_entries": _MAX_DATA_VALUE_SIZE_CACHE_ENTRIES,
            "hits": _DATA_VALUE_SIZE_STATS["hits"],
            "misses": _DATA_VALUE_SIZE_STATS["misses"],
        }


def _serialized_value_bytes(value: Any) -> int:
    """Byte count of the canonical JSON encoding of one push data value.

    The computation on a miss is byte-for-byte the original
    ``json.dumps(value, sort_keys=True, separators=(",", ":"))`` call, so the
    bound is unchanged. Only hashable, bounded scalar values are cached; every
    other input computes directly, exactly as before.
    """
    key: Optional[tuple] = None
    if value is None or isinstance(value, (bool, int, float)):
        key = (type(value), value)
    elif isinstance(value, str) and len(value) <= _MAX_DATA_VALUE_CACHE_KEY_CHARS:
        key = (str, value)
    if key is not None:
        with _DATA_VALUE_SIZE_CACHE_LOCK:
            cached = _DATA_VALUE_SIZE_CACHE.get(key)
            if cached is not None:
                _DATA_VALUE_SIZE_CACHE.move_to_end(key)
                _DATA_VALUE_SIZE_STATS["hits"] += 1
                return cached
            _DATA_VALUE_SIZE_STATS["misses"] += 1

    size = len(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    if key is not None:
        with _DATA_VALUE_SIZE_CACHE_LOCK:
            _DATA_VALUE_SIZE_CACHE[key] = size
            if len(_DATA_VALUE_SIZE_CACHE) > _MAX_DATA_VALUE_SIZE_CACHE_ENTRIES:
                _DATA_VALUE_SIZE_CACHE.popitem(last=False)
    return size


def _bounded_text(value: Any, field: str, maximum: int) -> str:
    text = str(value or "")
    if len(text) > maximum:
        raise ValueError(f"{field} exceeds maximum length of {maximum}")
    return text


def _bounded_data(value: Any) -> Dict[str, Any]:
    """Validate and bound the ``data`` payload shared by every fan-out target.

    Every transport receives this dict (FCM coerces values with ``str``, Web
    Push ``json.dumps`` it verbatim, APNs coerces and bounds it). An unbounded
    dict therefore multiplies its size by the number of devices/users in the
    fan-out. Reject oversized/non-serialisable data before any work is queued,
    and normalise non-string keys to strings so all three transports agree on
    the shape. Raises ``ValueError`` for malformed input (callers fail closed).
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("push data must be a mapping")
    if len(value) > MAX_PUSH_DATA_KEYS:
        raise ValueError(f"push data exceeds {MAX_PUSH_DATA_KEYS} keys")

    bounded: Dict[str, Any] = {}
    total = 0
    for raw_key, raw_value in value.items():
        key = str(raw_key)
        if not key or len(key) > MAX_PUSH_DATA_KEY_LENGTH:
            raise ValueError("push data key is empty or too long")
        try:
            # Shared id/type values are encoded once and reused across every
            # recipient of an announcement fan-out; the bound itself is still
            # derived from the value on every miss.
            value_bytes = _serialized_value_bytes(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError("push data value is not JSON serialisable") from exc
        if value_bytes > MAX_PUSH_DATA_VALUE_BYTES:
            raise ValueError(
                f"push data value exceeds maximum size of "
                f"{MAX_PUSH_DATA_VALUE_BYTES} bytes"
            )
        total += len(key.encode("utf-8")) + value_bytes
        if total > MAX_PUSH_DATA_TOTAL_BYTES:
            raise ValueError(
                f"push data exceeds maximum total size of "
                f"{MAX_PUSH_DATA_TOTAL_BYTES} bytes"
            )
        bounded[key] = raw_value
    return bounded


def _notification_server_id(data: Optional[Dict[str, Any]]) -> Optional[int]:
    """Return the server scope carried by a notification push payload.

    Notification payloads include ``server_id`` (stringified by
    ``stringify_snowflake_ids``). Notification preferences are per-server
    capable, so the ``mobile_push`` check must resolve the recipient's setting
    at the same scope the notification was generated for. Looking up only the
    global row silently discards a per-server ``mobile_push=False`` opt-out:
    the device still gets a ping the user explicitly turned off for that
    server. Non-notification push callers omit ``server_id`` and keep the
    historical global-only behaviour. Unknown/malformed values degrade to a
    global lookup rather than raising out of the delivery path.
    """
    if not data:
        return None
    raw = data.get("server_id")
    if raw is None:
        return None
    try:
        server_id = int(raw)
    except (TypeError, ValueError):
        return None
    return server_id if server_id > 0 else None


def _bounded_unique_users(user_ids, limit: int):
    """Dedupe a bulk audience (first-seen order) and hard-cap it.

    ``send_bulk_push`` used to do ``list(user_ids)[:MAX_BULK_PUSH_USERS]`` (and
    the async path ``[int(uid) for uid in user_ids][:MAX_BULK_PUSH_USERS]``).
    Both materialised/coerced the *entire* input before slicing, so a caller
    handing over a huge or highly-duplicated id list did unbounded work, and a
    repeated id produced one full device fan-out per occurrence (duplicate
    delivery). This helper walks at most ``limit`` *unique* ids, collapsing
    repeats as it goes and stopping the input stream early once the cap is
    reached — bounded work AND exactly one delivery per recipient.

    The input is streamed (never materialised) so generator callers stop being
    drained the moment the ceiling is hit. Values are deduped as-is; callers
    that need integer normalisation pass a lazy ``(int(uid) for uid in ...)``
    generator, which keeps the historical coercion behaviour without the
    unbounded up-front list build.
    """
    ceiling = max(1, int(limit))
    seen = set()
    ordered = []
    for uid in user_ids:
        if uid in seen:
            continue
        if len(ordered) >= ceiling:
            break
        seen.add(uid)
        ordered.append(uid)
    return ordered


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
        # Bound the per-device payload BEFORE the fan-out: these values are
        # copied into every one of the user's device requests.
        sound = _bounded_text(sound, "sound", MAX_SOUND_LENGTH)
        data = _bounded_data(data)

        tokens = self.get_user_tokens(user_id)
        if not tokens:
            return 0

        # A user cannot hold more than MAX_TOKENS_PER_USER tokens; apply the
        # same ceiling as send_push_async so a corrupt/lenient repository
        # cannot turn one notification into an unbounded device fan-out.
        tokens = list(tokens)[: self.MAX_TOKENS_PER_USER]
        if not tokens:
            return 0

        # Check user's notification settings at the server scope the payload
        # originated from. A per-server mobile_push=False must suppress the
        # device push even when no global settings row exists.
        if self._notifications:
            try:
                settings = self._notifications.get_notification_settings(
                    user_id, _notification_server_id(data)
                )
                if not settings.mobile_push:
                    return 0
            except Exception as e:
                # FAIL CLOSED: the settings lookup is the authorization gate for
                # a device push. Swallowing the error and continuing delivered
                # notifications to users whose mobile_push opt-out could not be
                # resolved (backend hiccup, corrupt/missing cache entry, or an
                # uninitialized notifications module). An unavailable preference
                # store must never silently grant delivery.
                logger.warning(
                    f"Push settings lookup failed; suppressing push (fail closed): {e}"
                )
                return 0

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
        # Same pre-fan-out bounds as the synchronous path.
        sound = _bounded_text(sound, "sound", MAX_SOUND_LENGTH)
        data = _bounded_data(data)

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
                    self._notifications.get_notification_settings,
                    user_id,
                    _notification_server_id(data),
                )
                if not settings.mobile_push:
                    return 0
            except Exception as e:
                # FAIL CLOSED: same gate as the synchronous path. A failed
                # preference lookup must suppress, never grant, device push.
                logger.warning(
                    f"Push settings lookup failed; suppressing push (fail closed): {e}"
                )
                return 0

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
        """Send push notification to multiple users (deduped and capped).

        A repeated user id produces one delivery, not one per occurrence, and
        the audience is streamed to the cap so an oversized input cannot make
        the caller do unbounded work before the bound is applied.
        """
        # Mirror send_bulk_push_async: a single bulk call may fan out to at
        # most MAX_BULK_PUSH_USERS so one caller cannot enqueue unbounded
        # work through the synchronous path (which would block the worker).
        # Validate the shared payload once, before any per-user work, so an
        # oversized blob cannot be re-serialised for every user in the batch.
        title = _bounded_text(title, "title", MAX_TITLE_LENGTH)
        body = _bounded_text(body, "body", MAX_BODY_LENGTH)
        data = _bounded_data(data)
        # Dedupe + hard-cap before any per-user work. A repeated id used to
        # trigger one full device fan-out per occurrence; streaming through
        # the helper also stops an oversized input early instead of
        # materialising the whole list just to slice it.
        users = _bounded_unique_users(user_ids, MAX_BULK_PUSH_USERS)
        total_sent = 0
        for user_id in users:
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
        deduped and capped so a single call cannot enqueue unbounded work or
        deliver the same notification to one user more than once.
        """
        title = _bounded_text(title, "title", MAX_TITLE_LENGTH)
        body = _bounded_text(body, "body", MAX_BODY_LENGTH)
        data = _bounded_data(data)
        # Same pre-fan-out dedupe + early cap as the synchronous path. The
        # ``int`` coercion stays lazy so a huge/duplicated stream is stopped
        # at the cap rather than being fully coerced before slicing; malformed
        # ids still raise as before (the check is not weakened).
        users = _bounded_unique_users(
            (int(uid) for uid in user_ids), MAX_BULK_PUSH_USERS
        )
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
